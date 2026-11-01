"""Usage, storage and publishing analytics computed from real records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.config import settings
from app.deps import Ctx, DbSession
from app.models import (
    Clip,
    Export,
    ExportKind,
    ExportStatus,
    JobStatus,
    MediaAsset,
    ProcessingJob,
    Project,
    PublishingAttempt,
    SocialPost,
    SocialPostStatus,
    UsageKind,
    UsageRecord,
)
from app.schemas import (
    AnalyticsOverview,
    DashboardStats,
    PlatformMetric,
    StorageSummary,
    UsageBreakdown,
)
from app.api.serializers import job_out

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _usage_totals(db, org_id) -> dict[str, float]:
    rows = db.execute(
        select(UsageRecord.kind, func.coalesce(func.sum(UsageRecord.quantity), 0))
        .where(UsageRecord.organization_id == org_id)
        .group_by(UsageRecord.kind)
    ).all()
    return {kind.value: float(total or 0) for kind, total in rows}


def _storage_summary(db, org_id) -> StorageSummary:
    media_bytes = db.execute(
        select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(
            MediaAsset.organization_id == org_id, MediaAsset.deleted_at.is_(None)
        )
    ).scalar_one()
    export_bytes = db.execute(
        select(func.coalesce(func.sum(Export.size_bytes), 0)).where(
            Export.organization_id == org_id, Export.status == ExportStatus.READY
        )
    ).scalar_one()
    media_count = db.execute(
        select(func.count(MediaAsset.id)).where(
            MediaAsset.organization_id == org_id, MediaAsset.deleted_at.is_(None)
        )
    ).scalar_one()
    export_count = db.execute(
        select(func.count(Export.id)).where(Export.organization_id == org_id)
    ).scalar_one()
    total = int(media_bytes) + int(export_bytes)
    quota = settings.storage_quota_mb * 1024 * 1024
    return StorageSummary(
        total_bytes=total,
        quota_bytes=quota,
        media_count=int(media_count),
        export_count=int(export_count),
        used_percent=round((total / quota) * 100, 2) if quota else 0.0,
    )


@router.get("/dashboard", response_model=DashboardStats)
def dashboard(db: DbSession, ctx: Ctx) -> DashboardStats:
    projects = db.execute(
        select(func.count(Project.id)).where(
            Project.organization_id == ctx.org_id, Project.deleted_at.is_(None)
        )
    ).scalar_one()
    clips = db.execute(
        select(func.count(Clip.id)).where(
            Clip.organization_id == ctx.org_id, Clip.deleted_at.is_(None)
        )
    ).scalar_one()
    exports = db.execute(
        select(func.count(Export.id)).where(
            Export.organization_id == ctx.org_id, Export.status == ExportStatus.READY
        )
    ).scalar_one()
    published = db.execute(
        select(func.count(SocialPost.id)).where(
            SocialPost.organization_id == ctx.org_id,
            SocialPost.status == SocialPostStatus.PUBLISHED,
        )
    ).scalar_one()
    scheduled = db.execute(
        select(func.count(SocialPost.id)).where(
            SocialPost.organization_id == ctx.org_id,
            SocialPost.status == SocialPostStatus.SCHEDULED,
        )
    ).scalar_one()
    failed_jobs = db.execute(
        select(func.count(ProcessingJob.id)).where(
            ProcessingJob.organization_id == ctx.org_id,
            ProcessingJob.status == JobStatus.FAILED,
            ProcessingJob.created_at >= datetime.now(timezone.utc) - timedelta(days=30),
        )
    ).scalar_one()
    usage = _usage_totals(db, ctx.org_id)
    recent_jobs = (
        db.execute(
            select(ProcessingJob)
            .where(ProcessingJob.organization_id == ctx.org_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    return DashboardStats(
        projects=int(projects),
        clips=int(clips),
        exports=int(exports),
        published_posts=int(published),
        scheduled_posts=int(scheduled),
        failed_jobs=int(failed_jobs),
        transcription_minutes=round(usage.get(UsageKind.TRANSCRIPTION_SECONDS.value, 0) / 60, 1),
        render_minutes=round(usage.get(UsageKind.RENDER_SECONDS.value, 0) / 60, 1),
        storage=_storage_summary(db, ctx.org_id),
        recent_jobs=[job_out(job) for job in recent_jobs],
    )


@router.get("/overview", response_model=AnalyticsOverview)
def overview(db: DbSession, ctx: Ctx) -> AnalyticsOverview:
    usage = _usage_totals(db, ctx.org_id)
    breakdown = [
        UsageBreakdown(kind=kind, quantity=value, unit="bytes" if kind == UsageKind.UPLOAD_BYTES else ("tokens" if kind == UsageKind.AI_TOKENS else "seconds"))
        for kind, value in usage.items()
    ]
    statuses = db.execute(
        select(SocialPost.status, func.count(SocialPost.id))
        .where(SocialPost.organization_id == ctx.org_id)
        .group_by(SocialPost.status)
    ).all()
    publishes_total = db.execute(
        select(func.count(PublishingAttempt.id)).where(
            PublishingAttempt.organization_id == ctx.org_id,
            PublishingAttempt.status == "succeeded",
        )
    ).scalar_one()
    failures = db.execute(
        select(func.count(PublishingAttempt.id)).where(
            PublishingAttempt.organization_id == ctx.org_id,
            PublishingAttempt.status == "failed",
        )
    ).scalar_one()
    platforms: list[PlatformMetric] = []
    from app.models import SocialAccount

    for account in db.execute(
        select(SocialAccount).where(SocialAccount.organization_id == ctx.org_id)
    ).scalars():
        platforms.append(
            PlatformMetric(
                platform=account.platform.value,
                status="available",
                detail="Connected — open Insights on the Social accounts page to pull API metrics.",
            )
        )
    if not platforms:
        platforms.append(
            PlatformMetric(
                platform="none",
                status="not_connected",
                detail="Connect YouTube or Mastodon to pull real engagement metrics. "
                "ClipFoam does not estimate engagement.",
            )
        )
    return AnalyticsOverview(
        usage=breakdown,
        platforms=platforms,
        publishes_total=int(publishes_total),
        publish_failures=int(failures),
        posts_by_status={status.value: int(count) for status, count in statuses},
    )


@router.get("/usage")
def usage(
    db: DbSession,
    ctx: Ctx,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(
            UsageRecord.kind,
            func.date(UsageRecord.created_at).label("day"),
            func.coalesce(func.sum(UsageRecord.quantity), 0),
        )
        .where(UsageRecord.organization_id == ctx.org_id, UsageRecord.created_at >= since)
        .group_by(UsageRecord.kind, "day")
        .order_by("day")
    ).all()
    series: list[dict] = []
    for kind, day, total in rows:
        series.append({"kind": kind.value, "day": str(day), "quantity": float(total or 0)})
    totals = _usage_totals(db, ctx.org_id)
    return {
        "series": series,
        "totals": totals,
        "limits": {
            "transcription_minutes": settings.usage_free_transcription_minutes,
            "render_minutes": settings.usage_free_render_minutes,
            "storage_mb": settings.storage_quota_mb,
        },
        "storage": _storage_summary(db, ctx.org_id).model_dump(),
    }


@router.get("/exports")
def export_history(
    db: DbSession,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    rows = db.execute(
        select(Export)
        .where(Export.organization_id == ctx.org_id)
        .order_by(Export.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return [
        {
            "id": str(export.id),
            "kind": export.kind.value,
            "status": export.status.value,
            "size_bytes": export.size_bytes,
            "duration_seconds": export.duration_seconds,
            "width": export.width,
            "height": export.height,
            "created_at": export.created_at,
            "completed_at": export.completed_at,
            "clip_id": str(export.clip_id) if export.clip_id else None,
            "project_id": str(export.project_id),
            "is_video": export.kind == ExportKind.VIDEO,
        }
        for export in rows
    ]
