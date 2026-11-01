"""Social accounts, OAuth connection, drafts, scheduling and publishing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, PermissionDenied, PublishError, ValidationError
from app.models import (
    Export,
    ExportKind,
    ExportStatus,
    JobStatus,
    JobType,
    ProcessingJob,
    PublishAttemptStatus,
    PublishingAttempt,
    ScheduledPost,
    ScheduledPostStatus,
    SocialAccount,
    SocialAccountStatus,
    SocialPlatform,
    SocialPost,
    SocialPostStatus,
)
from app.schemas import (
    ConnectStartResponse,
    PublishRequest,
    PublishingAttemptOut,
    ScheduledPostOut,
    SocialAccountOut,
    SocialPostCreate,
    SocialPostOut,
    SocialPostUpdate,
    SocialProviderInfo,
)
from app.security import encrypt_secret
from app.services.audit import record_audit
from app.services.publishing import (
    PublishError as PublisherError,
    create_oauth_state,
    get_publisher,
    list_providers,
    read_oauth_state,
    redirect_uri_for,
)
from app.api.serializers import job_out, social_post_out
from app.workers.queue import enqueue

router = APIRouter(prefix="/social", tags=["social"])


def _post_or_404(db, org_id: uuid.UUID, post_id: uuid.UUID) -> SocialPost:
    post = db.execute(
        select(SocialPost).where(SocialPost.id == post_id, SocialPost.organization_id == org_id)
    ).scalar_one_or_none()
    if post is None:
        raise NotFoundError("Post not found", code="post_not_found")
    return post


def _hydrate(post: SocialPost, db) -> SocialPostOut:
    payload = social_post_out(post)
    attempts = (
        db.execute(
            select(PublishingAttempt)
            .where(PublishingAttempt.social_post_id == post.id)
            .order_by(PublishingAttempt.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    payload.attempts = [PublishingAttemptOut.model_validate(attempt) for attempt in attempts]
    return payload


@router.get("/providers", response_model=list[SocialProviderInfo])
def providers() -> list[SocialProviderInfo]:
    return [SocialProviderInfo(**provider) for provider in list_providers()]


@router.post("/connect/{platform}", response_model=ConnectStartResponse)
def start_connection(
    platform: str, request: Request, db: DbSession, ctx: WriteCtx
) -> ConnectStartResponse:
    verify_csrf(request)
    if not ctx.is_admin():
        raise PermissionDenied("Only workspace admins can connect social accounts.")
    publisher = get_publisher(platform)
    state = create_oauth_state(
        organization_id=ctx.org_id, user_id=ctx.user.id, platform=platform
    )
    try:
        url = publisher.authorization_url(state=state, redirect_uri=redirect_uri_for(platform))
    except PublisherError as exc:
        raise PublishError(exc.message, code=exc.code) from exc
    record_audit(
        db,
        action="social.connect_started",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="social_account",
        metadata={"platform": platform},
        request=request,
    )
    db.commit()
    return ConnectStartResponse(authorization_url=url, state=state)


@router.get("/callback/{platform}")
def oauth_callback(
    platform: str,
    db: DbSession,
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    """OAuth redirect target. Browser lands here, then is sent back to the app."""
    frontend = settings.public_app_url.rstrip("/")
    if error:
        return RedirectResponse(f"{frontend}/social-accounts?error={error}")
    if not code or not state:
        return RedirectResponse(f"{frontend}/social-accounts?error=missing_code")
    try:
        claims = read_oauth_state(state)
    except PublisherError as exc:
        return RedirectResponse(f"{frontend}/social-accounts?error={exc.code}")
    if claims.get("platform") != platform:
        return RedirectResponse(f"{frontend}/social-accounts?error=platform_mismatch")
    organization_id = uuid.UUID(claims["org"])
    user_id = uuid.UUID(claims["sub"])
    publisher = get_publisher(platform)
    try:
        tokens = publisher.exchange_code(code=code, redirect_uri=redirect_uri_for(platform))
    except PublisherError as exc:
        record_audit(
            db,
            action="social.connect_failed",
            organization_id=organization_id,
            actor_user_id=user_id,
            metadata={"platform": platform, "error": exc.message[:300]},
        )
        db.commit()
        return RedirectResponse(f"{frontend}/social-accounts?error={exc.code}")
    account = db.execute(
        select(SocialAccount).where(
            SocialAccount.organization_id == organization_id,
            SocialAccount.platform == SocialPlatform(platform),
            SocialAccount.external_account_id == tokens.external_account_id,
        )
    ).scalar_one_or_none()
    if account is None:
        account = SocialAccount(
            organization_id=organization_id,
            platform=SocialPlatform(platform),
            external_account_id=tokens.external_account_id,
            display_name=tokens.display_name,
        )
        db.add(account)
    account.display_name = tokens.display_name
    account.avatar_url = tokens.avatar_url
    account.access_token_encrypted = encrypt_secret(tokens.access_token)
    account.refresh_token_encrypted = (
        encrypt_secret(tokens.refresh_token) if tokens.refresh_token else None
    )
    account.token_expires_at = tokens.expires_at
    account.scopes = tokens.scopes
    account.status = SocialAccountStatus.CONNECTED
    account.metadata_json = tokens.metadata
    account.connected_by_id = user_id
    record_audit(
        db,
        action="social.connected",
        organization_id=organization_id,
        actor_user_id=user_id,
        target_type="social_account",
        target_id=str(account.id),
        metadata={"platform": platform},
    )
    db.commit()
    return RedirectResponse(f"{frontend}/social-accounts?connected={platform}")


@router.get("/accounts", response_model=list[SocialAccountOut])
def list_accounts(db: DbSession, ctx: Ctx) -> list[SocialAccountOut]:
    accounts = (
        db.execute(
            select(SocialAccount)
            .where(SocialAccount.organization_id == ctx.org_id)
            .order_by(SocialAccount.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [SocialAccountOut.model_validate(account) for account in accounts]


@router.delete("/accounts/{account_id}", status_code=204, response_model=None)
def disconnect_account(
    account_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx
) -> None:
    verify_csrf(request)
    account = db.execute(
        select(SocialAccount).where(
            SocialAccount.id == account_id, SocialAccount.organization_id == ctx.org_id
        )
    ).scalar_one_or_none()
    if account is None:
        raise NotFoundError("Social account not found", code="account_not_found")
    record_audit(
        db,
        action="social.disconnected",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="social_account",
        target_id=str(account.id),
        metadata={"platform": account.platform.value},
        request=request,
    )
    db.delete(account)
    db.commit()
    return None


@router.get("/posts", response_model=list[SocialPostOut])
def list_posts(
    db: DbSession,
    ctx: Ctx,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[SocialPostOut]:
    stmt = select(SocialPost).where(SocialPost.organization_id == ctx.org_id)
    if status_filter:
        try:
            stmt = stmt.where(SocialPost.status == SocialPostStatus(status_filter))
        except ValueError as exc:
            raise ValidationError(f"Unknown post status: {status_filter}") from exc
    if project_id:
        stmt = stmt.where(SocialPost.project_id == project_id)
    posts = (
        db.execute(stmt.order_by(SocialPost.created_at.desc()).limit(limit)).scalars().all()
    )
    return [_hydrate(post, db) for post in posts]


@router.post("/posts", response_model=SocialPostOut, status_code=201)
def create_post(
    payload: SocialPostCreate, request: Request, db: DbSession, ctx: WriteCtx
) -> SocialPostOut:
    verify_csrf(request)
    if payload.export_id:
        export = db.execute(
            select(Export).where(
                Export.id == payload.export_id, Export.organization_id == ctx.org_id
            )
        ).scalar_one_or_none()
        if export is None:
            raise NotFoundError("Export not found", code="export_not_found")
        if export.status != ExportStatus.READY:
            raise ValidationError("Export is not ready to publish", code="export_not_ready")
        if payload.platform.value == "youtube" and export.kind != ExportKind.VIDEO:
            raise ValidationError("YouTube requires a video export", code="invalid_export_kind")
    account_id = payload.social_account_id
    if account_id is None:
        account = (
            db.execute(
                select(SocialAccount).where(
                    SocialAccount.organization_id == ctx.org_id,
                    SocialAccount.platform == payload.platform,
                    SocialAccount.status == SocialAccountStatus.CONNECTED,
                )
            )
            .scalars()
            .first()
        )
        account_id = account.id if account else None
    post = SocialPost(
        organization_id=ctx.org_id,
        project_id=payload.project_id,
        clip_id=payload.clip_id,
        export_id=payload.export_id,
        social_account_id=account_id,
        created_by_id=ctx.user.id,
        platform=payload.platform,
        title=payload.title,
        body=payload.body,
        hashtags=payload.hashtags,
        status=SocialPostStatus.DRAFT,
        scheduled_at=payload.scheduled_at,
        timezone=payload.timezone,
        platform_metadata=payload.platform_metadata,
        idempotency_key=f"{ctx.org_id}:{payload.platform.value}:{uuid.uuid4().hex}",
    )
    db.add(post)
    db.flush()
    if payload.scheduled_at is not None:
        db.add(
            ScheduledPost(
                organization_id=ctx.org_id,
                social_post_id=post.id,
                scheduled_for=payload.scheduled_at,
                timezone=payload.timezone,
                status=ScheduledPostStatus.PENDING,
            )
        )
        post.status = SocialPostStatus.SCHEDULED
    record_audit(
        db,
        action="post.created",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="social_post",
        target_id=str(post.id),
        metadata={"platform": payload.platform.value, "scheduled": bool(payload.scheduled_at)},
        request=request,
    )
    db.commit()
    return _hydrate(post, db)


@router.patch("/posts/{post_id}", response_model=SocialPostOut)
def update_post(
    post_id: uuid.UUID, payload: SocialPostUpdate, request: Request, db: DbSession, ctx: WriteCtx
) -> SocialPostOut:
    verify_csrf(request)
    post = _post_or_404(db, ctx.org_id, post_id)
    if post.status == SocialPostStatus.PUBLISHED:
        raise ValidationError("Published posts cannot be edited", code="post_already_published")
    data = payload.model_dump(exclude_unset=True)
    for field in ("title", "body", "hashtags", "timezone", "platform_metadata", "social_account_id"):
        if field in data and data[field] is not None:
            setattr(post, field, data[field])
    if "scheduled_at" in data:
        post.scheduled_at = data["scheduled_at"]
        scheduled = db.execute(
            select(ScheduledPost).where(ScheduledPost.social_post_id == post.id)
        ).scalar_one_or_none()
        if post.scheduled_at is None:
            if scheduled is not None:
                db.delete(scheduled)
            if post.status == SocialPostStatus.SCHEDULED:
                post.status = SocialPostStatus.DRAFT
        else:
            if scheduled is None:
                scheduled = ScheduledPost(
                    organization_id=ctx.org_id,
                    social_post_id=post.id,
                    scheduled_for=post.scheduled_at,
                    timezone=post.timezone,
                )
                db.add(scheduled)
            scheduled.scheduled_for = post.scheduled_at
            scheduled.status = ScheduledPostStatus.PENDING
            post.status = SocialPostStatus.SCHEDULED
    record_audit(
        db,
        action="post.updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="social_post",
        target_id=str(post.id),
        request=request,
    )
    db.commit()
    return _hydrate(post, db)


@router.delete("/posts/{post_id}", status_code=204, response_model=None)
def delete_post(post_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> None:
    verify_csrf(request)
    post = _post_or_404(db, ctx.org_id, post_id)
    if post.status == SocialPostStatus.PUBLISHING:
        raise ValidationError("A publishing job is in progress", code="post_publishing")
    db.delete(post)
    record_audit(
        db,
        action="post.deleted",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="social_post",
        target_id=str(post.id),
        request=request,
    )
    db.commit()
    return None


@router.post("/posts/{post_id}/publish", response_model=dict, status_code=202)
def publish_post(
    post_id: uuid.UUID,
    payload: PublishRequest | None,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> dict:
    verify_csrf(request)
    if not settings.social_publish_enabled:
        raise ValidationError(
            "Social publishing is disabled by configuration (CF_SOCIAL_PUBLISH_ENABLED=false)",
            code="publishing_disabled",
        )
    post = _post_or_404(db, ctx.org_id, post_id)
    if post.status == SocialPostStatus.PUBLISHED:
        raise ValidationError("This post has already been published", code="already_published")
    if post.status == SocialPostStatus.PUBLISHING:
        raise ValidationError("This post is already publishing", code="already_publishing")
    account_id = payload.social_account_id if payload else None
    if account_id:
        account = db.execute(
            select(SocialAccount).where(
                SocialAccount.id == account_id, SocialAccount.organization_id == ctx.org_id
            )
        ).scalar_one_or_none()
        if account is None:
            raise NotFoundError("Social account not found", code="account_not_found")
        if account.status != SocialAccountStatus.CONNECTED:
            raise ValidationError(
                f"The {account.platform.value} connection needs to be re-authorized",
                code="account_not_connected",
            )
        post.social_account_id = account.id
    if post.platform.value == "youtube" and post.export_id is None:
        raise ValidationError(
            "YouTube publishing requires a rendered export. Render the clip first.",
            code="missing_export",
        )
    job = enqueue(
        db,
        organization_id=ctx.org_id,
        job_type=JobType.SOCIAL_PUBLISH,
        payload={
            "social_post_id": str(post.id),
            "social_account_id": str(account_id) if account_id else None,
        },
        social_post_id=post.id,
        project_id=post.project_id,
        created_by_id=ctx.user.id,
        idempotency_key=f"publish:{post.idempotency_key}",
    )
    post.status = SocialPostStatus.PUBLISHING
    record_audit(
        db,
        action="post.publish_requested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="social_post",
        target_id=str(post.id),
        metadata={"job_id": str(job.id)},
        request=request,
    )
    db.commit()
    return {"job": job_out(job).model_dump(mode="json"), "post": _hydrate(post, db).model_dump(mode="json")}


@router.post("/posts/{post_id}/schedule", response_model=ScheduledPostOut, status_code=201)
def schedule_post(
    post_id: uuid.UUID,
    scheduled_for: datetime,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
    timezone_name: Annotated[str, Query(max_length=64)] = "UTC",
) -> ScheduledPostOut:
    verify_csrf(request)
    post = _post_or_404(db, ctx.org_id, post_id)
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    if scheduled_for < datetime.now(timezone.utc) - timedelta(minutes=1):
        raise ValidationError("Scheduled time must be in the future", code="schedule_in_past")
    scheduled = db.execute(
        select(ScheduledPost).where(ScheduledPost.social_post_id == post.id)
    ).scalar_one_or_none()
    if scheduled is None:
        scheduled = ScheduledPost(
            organization_id=ctx.org_id,
            social_post_id=post.id,
            scheduled_for=scheduled_for,
            timezone=timezone_name,
        )
        db.add(scheduled)
    scheduled.scheduled_for = scheduled_for
    scheduled.timezone = timezone_name
    scheduled.status = ScheduledPostStatus.PENDING
    post.scheduled_at = scheduled_for
    post.timezone = timezone_name
    post.status = SocialPostStatus.SCHEDULED
    db.commit()
    return ScheduledPostOut.model_validate(scheduled)


@router.delete("/posts/{post_id}/schedule", status_code=204, response_model=None)
def unschedule_post(
    post_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx
) -> None:
    verify_csrf(request)
    post = _post_or_404(db, ctx.org_id, post_id)
    scheduled = db.execute(
        select(ScheduledPost).where(ScheduledPost.social_post_id == post.id)
    ).scalar_one_or_none()
    if scheduled is not None:
        db.delete(scheduled)
    post.scheduled_at = None
    if post.status == SocialPostStatus.SCHEDULED:
        post.status = SocialPostStatus.DRAFT
    db.commit()
    return None


@router.get("/calendar", response_model=list[SocialPostOut])
def calendar(
    db: DbSession,
    ctx: Ctx,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> list[SocialPostOut]:
    start = start or datetime.now(timezone.utc) - timedelta(days=7)
    end = end or datetime.now(timezone.utc) + timedelta(days=30)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if end <= start:
        raise ValidationError("Calendar end must be after start", code="invalid_calendar_range")
    if (end - start).days > 366:
        raise ValidationError("Calendar range is limited to one year", code="calendar_range_too_large")
    posts = (
        db.execute(
            select(SocialPost).where(
                SocialPost.organization_id == ctx.org_id,
                (
                    (SocialPost.scheduled_at.is_not(None))
                    & (SocialPost.scheduled_at >= start)
                    & (SocialPost.scheduled_at <= end)
                )
                | (
                    (SocialPost.published_at.is_not(None))
                    & (SocialPost.published_at >= start)
                    & (SocialPost.published_at <= end)
                ),
            )
        )
        .scalars()
        .all()
    )
    return [_hydrate(post, db) for post in posts]


@router.get("/posts/{post_id}/attempts", response_model=list[PublishingAttemptOut])
def list_attempts(post_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[PublishingAttemptOut]:
    post = _post_or_404(db, ctx.org_id, post_id)
    attempts = (
        db.execute(
            select(PublishingAttempt)
            .where(PublishingAttempt.social_post_id == post.id)
            .order_by(PublishingAttempt.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [PublishingAttemptOut.model_validate(attempt) for attempt in attempts]


@router.get("/metrics")
def platform_metrics(db: DbSession, ctx: Ctx) -> list[dict]:
    """Only real, API-backed metrics. Unavailable metrics are reported as such."""
    from app.security import decrypt_secret
    from app.services.publishing import IMPLEMENTED

    results: list[dict] = []
    accounts = (
        db.execute(select(SocialAccount).where(SocialAccount.organization_id == ctx.org_id))
        .scalars()
        .all()
    )
    for account in accounts:
        publisher = IMPLEMENTED.get(account.platform.value)
        if publisher is None:
            results.append(
                {
                    "platform": account.platform.value,
                    "status": "unsupported",
                    "detail": "No metrics API implemented for this platform",
                    "metrics": {},
                }
            )
            continue
        if not account.scopes or "read" not in " ".join(map(str, account.scopes)):
            results.append(
                {
                    "platform": account.platform.value,
                    "status": "permission_missing",
                    "detail": "Read scope not granted when the account was connected",
                    "metrics": {},
                }
            )
            continue
        posts = (
            db.execute(
                select(SocialPost).where(
                    SocialPost.social_account_id == account.id,
                    SocialPost.status == SocialPostStatus.PUBLISHED,
                    SocialPost.published_external_id.is_not(None),
                )
            )
            .scalars()
            .all()
        )
        totals: dict[str, float] = {}
        errors: list[str] = []
        try:
            token = decrypt_secret(account.access_token_encrypted)
        except ValueError as exc:
            results.append(
                {
                    "platform": account.platform.value,
                    "status": "permission_missing",
                    "detail": str(exc),
                    "metrics": {},
                }
            )
            continue
        for post in posts[:10]:
            try:
                data = publisher.fetch_metrics(access_token=token, external_id=post.published_external_id or "")
            except PublisherError as exc:
                errors.append(exc.message)
                continue
            for key, value in data.items():
                totals[key] = totals.get(key, 0.0) + value
        if errors and not totals:
            results.append(
                {
                    "platform": account.platform.value,
                    "status": "permission_missing",
                    "detail": errors[0][:300],
                    "metrics": {},
                }
            )
        else:
            results.append(
                {
                    "platform": account.platform.value,
                    "status": "available",
                    "detail": None if not errors else errors[0][:300],
                    "metrics": totals,
                }
            )
    return results


_ = (ProcessingJob, JobStatus, PublishAttemptStatus, job_out)
