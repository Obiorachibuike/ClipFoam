"""Runtime configuration, health, provider tests, audit log and account deletion."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.deps import AdminCtx, Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import PermissionDenied
from app.models import AuditLog, JobType, MediaAsset, MediaStatus
from app.schemas import AuditLogOut, Page, ProviderStatus, RuntimeConfigOut
from app.services.ai import provider_status, test_provider
from app.services.audit import record_audit
from app.services.media import ffmpeg_available, ffprobe_available
from app.services.transcription import get_transcription_provider
from app.storage import disk_free_mb
from app.workers.queue import enqueue

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, object]:
    from sqlalchemy import text

    from app.db import engine

    db_ok = True
    db_error = None
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        db_error = str(exc)[:300]
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "env": settings.app_env,
        "database": {"ok": db_ok, "error": db_error},
        "ffmpeg": ffmpeg_available(),
        "ffprobe": ffprobe_available(),
        "storage_backend": settings.storage_backend,
        "disk_free_mb": disk_free_mb(),
    }


@router.get("/system/config", response_model=RuntimeConfigOut)
def runtime_config(db: DbSession, ctx: Ctx) -> RuntimeConfigOut:
    provider_ok, provider_detail = get_transcription_provider().available()
    ai = provider_status()
    transcription = ProviderStatus(
        id=settings.transcription_provider,
        label="faster-whisper (local)" if settings.transcription_provider == "faster_whisper" else "Manual import",
        configured=provider_ok,
        detail=provider_detail
        or f"Model: {settings.whisper_model} · device: {settings.whisper_device} · compute: {settings.whisper_compute_type}",
    )
    storage_status = ProviderStatus(
        id=settings.storage_backend,
        label="S3-compatible object storage" if settings.storage_backend == "s3" else "Local disk (development)",
        configured=True,
        detail=settings.s3_bucket if settings.storage_backend == "s3" else str(settings.local_storage_path),
    )
    return RuntimeConfigOut(
        app_env=settings.app_env,
        max_upload_mb=settings.max_upload_mb,
        max_duration_seconds=settings.max_duration_seconds,
        allowed_video_extensions=sorted(settings.video_extensions),
        allowed_audio_extensions=sorted(settings.audio_extensions),
        transcription=transcription,
        ai=ProviderStatus(**ai),
        storage=storage_status,
        ffmpeg_available=ffmpeg_available(),
        ffprobe_available=ffprobe_available(),
        enable_signup=settings.enable_signup,
        social_publish_enabled=settings.social_publish_enabled,
        retention_days=settings.retention_days,
        usage_limits={
            "storage_mb": settings.storage_quota_mb,
            "transcription_minutes": settings.usage_free_transcription_minutes,
            "render_minutes": settings.usage_free_render_minutes,
            "worker_concurrency": settings.worker_concurrency,
        },
    )


class ProviderTestRequest(BaseModel):
    provider: str | None = None


@router.post("/system/providers/test")
def test_ai_provider(
    payload: ProviderTestRequest, request: Request, db: DbSession, ctx: AdminCtx
) -> dict:
    verify_csrf(request)
    result = test_provider(payload.provider)
    record_audit(
        db,
        action="provider.tested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        metadata={"ok": result.get("ok"), "provider": result.get("provider")},
        request=request,
    )
    db.commit()
    return result


@router.get("/system/audit-logs", response_model=Page[AuditLogOut])
def audit_logs(
    db: DbSession,
    ctx: AdminCtx,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AuditLogOut]:
    stmt = select(AuditLog).where(AuditLog.organization_id == ctx.org_id)
    entries = (
        db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(entry) for entry in entries],
        total=len(entries),
        limit=limit,
        offset=offset,
    )


class DeleteAccountRequest(BaseModel):
    confirm: str


@router.delete("/account", status_code=202)
def delete_account(
    payload: DeleteAccountRequest, request: Request, db: DbSession, ctx: WriteCtx
) -> dict:
    """Schedule deletion of the workspace and, for solo accounts, the user."""
    verify_csrf(request)
    if payload.confirm != "DELETE":
        raise PermissionDenied("Type DELETE to confirm account removal", code="confirmation_required")
    if not ctx.is_admin():
        raise PermissionDenied("Only workspace admins can delete the workspace.")
    media = db.execute(
        select(MediaAsset).where(MediaAsset.organization_id == ctx.org_id)
    ).scalars().all()
    _ = [item for item in media if item.status == MediaStatus.READY]
    job = enqueue(
        db,
        organization_id=ctx.org_id,
        job_type=JobType.ACCOUNT_DELETE,
        payload={"organization_id": str(ctx.org_id), "user_id": str(ctx.user.id)},
        created_by_id=ctx.user.id,
        idempotency_key=f"account-delete:{ctx.org_id}",
    )
    record_audit(
        db,
        action="account.deletion_requested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        request=request,
    )
    db.commit()
    return {"scheduled": True, "job_id": str(job.id)}


_ = uuid
