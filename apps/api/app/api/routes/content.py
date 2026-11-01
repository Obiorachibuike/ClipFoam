"""AI Content Studio: generation, editing and persistence of copy."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, ProviderUnavailable
from app.models import (
    Clip,
    ContentItem,
    ContentKind,
    MediaAsset,
    Project,
    Transcript,
    UsageKind,
)
from app.schemas import ContentGenerateRequest, ContentItemOut, ContentItemUpdate
from app.services import content as content_service
from app.services.audit import record_audit, record_usage
from app.services.clip_service import clip_duration
from app.api.routes.projects import get_project
from app.api.routes.clips import get_clip, _segments_for_clip

router = APIRouter(tags=["content"])


def _persist(
    db,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    clip_id: uuid.UUID | None,
    outcome: content_service.GenerationOutcome,
) -> list[ContentItem]:
    items: list[ContentItem] = []
    for generated in outcome.items:
        item = ContentItem(
            organization_id=org_id,
            project_id=project_id,
            clip_id=clip_id,
            kind=generated.kind,
            title=generated.title,
            body=generated.body,
            provider=outcome.provider,
            model=outcome.model,
            metadata_json=generated.metadata,
        )
        db.add(item)
        items.append(item)
    db.flush()
    return items


@router.post("/clips/{clip_id}/content/generate", response_model=list[ContentItemOut], status_code=201)
def generate_clip_content(
    clip_id: uuid.UUID,
    payload: ContentGenerateRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> list[ContentItemOut]:
    verify_csrf(request)
    clip = get_clip(db, ctx.org_id, clip_id)
    project = db.get(Project, clip.project_id)
    if project is None:
        raise NotFoundError("Project not found", code="project_not_found")

    segments = _segments_for_clip(db, clip)
    excerpt = " ".join(str(segment["text"]) for segment in segments).strip()
    if not excerpt:
        excerpt = clip.description or clip.title
    provider = content_service.provider_or_none(payload.provider)
    if provider is None:
        outcome = content_service.deterministic_clip_content(
            excerpt=excerpt,
            title=clip.title,
            platform=payload.platform or clip.target_platform,
            tone=payload.tone,
            kinds=payload.kinds,
            count=payload.count,
        )
    else:
        try:
            outcome = content_service.generate_with_ai(
                provider,
                kinds=payload.kinds,
                transcript_excerpt=excerpt,
                context_title=clip.title,
                platform=payload.platform or clip.target_platform,
                tone=payload.tone,
                count=payload.count,
            )
        except ProviderUnavailable as exc:
            record_audit(
                db,
                action="content.generation_failed",
                organization_id=ctx.org_id,
                actor_user_id=ctx.user.id,
                target_type="clip",
                target_id=str(clip.id),
                metadata={"code": exc.code},
                request=request,
            )
            db.commit()
            raise
        if outcome.tokens:
            record_usage(
                db,
                organization_id=ctx.org_id,
                kind=UsageKind.AI_TOKENS,
                quantity=float(outcome.tokens),
                unit="tokens",
                project_id=project.id,
                user_id=ctx.user.id,
                provider=outcome.provider,
                metadata={"model": outcome.model, "kind": "clip_content"},
            )
    items = _persist(
        db,
        org_id=ctx.org_id,
        project_id=project.id,
        clip_id=clip.id,
        outcome=outcome,
    )
    record_audit(
        db,
        action="content.generated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip",
        target_id=str(clip.id),
        metadata={"provider": outcome.provider, "kinds": [k.value for k in payload.kinds]},
        request=request,
    )
    db.commit()
    return [ContentItemOut.model_validate(item) for item in items]


@router.post("/projects/{project_id}/content/generate", response_model=list[ContentItemOut], status_code=201)
def generate_project_content(
    project_id: uuid.UUID,
    payload: ContentGenerateRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> list[ContentItemOut]:
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    media = (
        db.execute(
            select(MediaAsset)
            .where(MediaAsset.project_id == project.id)
            .order_by(MediaAsset.created_at.desc())
        )
        .scalars()
        .first()
    )
    if media is None:
        raise NotFoundError("No media in this project", code="no_media")
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == media.id)
    ).scalar_one_or_none()
    if transcript is None or transcript.status.value != "ready":
        raise ProviderUnavailable(
            "Generate the transcript first — long-form copy is grounded in the transcript.",
            code="transcript_required",
        )
    segments = [
        {
            "start_seconds": segment.start_seconds,
            "end_seconds": segment.end_seconds,
            "text": segment.text,
        }
        for segment in transcript.segments
    ]
    full_text = " ".join(segment["text"] for segment in segments)
    provider = content_service.provider_or_none(payload.provider)
    if provider is None:
        outcome = content_service.deterministic_project_content(
            segments=segments,
            project_title=project.title,
            kinds=payload.kinds,
        )
    else:
        outcome = content_service.generate_with_ai(
            provider,
            kinds=payload.kinds,
            transcript_excerpt=full_text,
            context_title=project.title,
            platform=payload.platform,
            tone=payload.tone,
            count=payload.count,
            is_clip=False,
        )
        if outcome.tokens:
            record_usage(
                db,
                organization_id=ctx.org_id,
                kind=UsageKind.AI_TOKENS,
                quantity=float(outcome.tokens),
                unit="tokens",
                project_id=project.id,
                user_id=ctx.user.id,
                provider=outcome.provider,
                metadata={"model": outcome.model, "kind": "project_content"},
            )
    items = _persist(db, org_id=ctx.org_id, project_id=project.id, clip_id=None, outcome=outcome)
    record_audit(
        db,
        action="content.generated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="project",
        target_id=str(project.id),
        metadata={"provider": outcome.provider},
        request=request,
    )
    db.commit()
    return [ContentItemOut.model_validate(item) for item in items]


@router.get("/clips/{clip_id}/content", response_model=list[ContentItemOut])
def list_clip_content(clip_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[ContentItemOut]:
    clip = get_clip(db, ctx.org_id, clip_id)
    items = (
        db.execute(
            select(ContentItem)
            .where(ContentItem.clip_id == clip.id)
            .order_by(ContentItem.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [ContentItemOut.model_validate(item) for item in items]


@router.get("/projects/{project_id}/content", response_model=list[ContentItemOut])
def list_project_content(project_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[ContentItemOut]:
    project = get_project(db, ctx.org_id, project_id)
    items = (
        db.execute(
            select(ContentItem)
            .where(ContentItem.project_id == project.id)
            .order_by(ContentItem.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [ContentItemOut.model_validate(item) for item in items]


@router.patch("/content/{item_id}", response_model=ContentItemOut)
def update_content(
    item_id: uuid.UUID,
    payload: ContentItemUpdate,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> ContentItemOut:
    verify_csrf(request)
    item = db.execute(
        select(ContentItem).where(
            ContentItem.id == item_id, ContentItem.organization_id == ctx.org_id
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("Content item not found", code="content_not_found")
    if payload.title is not None:
        item.title = payload.title
    if payload.body is not None:
        item.body = payload.body
        item.is_edited = True
        item.version += 1
    record_audit(
        db,
        action="content.updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="content_item",
        target_id=str(item.id),
        request=request,
    )
    db.commit()
    return ContentItemOut.model_validate(item)


@router.delete("/content/{item_id}", status_code=204, response_model=None)
def delete_content(item_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> None:
    verify_csrf(request)
    item = db.execute(
        select(ContentItem).where(
            ContentItem.id == item_id, ContentItem.organization_id == ctx.org_id
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("Content item not found", code="content_not_found")
    db.delete(item)
    db.commit()
    return None


_ = (Clip, ContentKind, clip_duration)
