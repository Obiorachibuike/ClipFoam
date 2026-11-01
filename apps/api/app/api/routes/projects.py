"""Project CRUD, scoped to the caller's workspace."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, ValidationError
from app.models import (
    Clip,
    ClipCandidate,
    ClipStatus,
    Export,
    JobStatus,
    JobType,
    MediaAsset,
    MediaStatus,
    ProcessingJob,
    Project,
    ProjectStatus,
    Transcript,
    TranscriptStatus,
)
from app.schemas import (
    JobOut,
    MediaAssetOut,
    Page,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectUpdate,
)
from app.services.audit import record_audit
from app.api.serializers import is_active_job, job_out, media_out
from app.workers.queue import enqueue

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project(db: Session, org_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.organization_id == org_id,
            Project.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found", code="project_not_found")
    return project


def _counts(db: Session, project: Project) -> dict[str, int]:
    media_count = db.execute(
        select(func.count(MediaAsset.id)).where(
            MediaAsset.project_id == project.id, MediaAsset.deleted_at.is_(None)
        )
    ).scalar_one()
    clip_count = db.execute(
        select(func.count(Clip.id)).where(Clip.project_id == project.id, Clip.deleted_at.is_(None))
    ).scalar_one()
    candidate_count = db.execute(
        select(func.count(ClipCandidate.id)).where(ClipCandidate.project_id == project.id)
    ).scalar_one()
    export_count = db.execute(
        select(func.count(Export.id)).where(Export.project_id == project.id)
    ).scalar_one()
    return {
        "media_count": int(media_count),
        "clip_count": int(clip_count),
        "candidate_count": int(candidate_count),
        "export_count": int(export_count),
    }


def _detail(db: Session, project: Project) -> ProjectDetail:
    counts = _counts(db, project)
    media = db.execute(
        select(MediaAsset)
        .where(MediaAsset.project_id == project.id, MediaAsset.deleted_at.is_(None))
        .order_by(MediaAsset.created_at.desc())
    ).scalars().first()
    transcript_status = None
    if media is not None:
        transcript = db.execute(
            select(Transcript).where(Transcript.media_asset_id == media.id)
        ).scalar_one_or_none()
        transcript_status = transcript.status.value if transcript else None
    active_jobs = (
        db.execute(
            select(ProcessingJob)
            .where(
                ProcessingJob.project_id == project.id,
                ProcessingJob.status.notin_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]),
            )
            .order_by(ProcessingJob.created_at.desc())
        )
        .scalars()
        .all()
    )
    detail = ProjectDetail.model_validate(project, from_attributes=True)
    detail.media_count = counts["media_count"]
    detail.clip_count = counts["clip_count"]
    detail.candidate_count = counts["candidate_count"]
    detail.export_count = counts["export_count"]
    detail.primary_media = media_out(media) if media else None
    detail.transcript_status = transcript_status
    detail.active_jobs = [job_out(job) for job in active_jobs]
    return detail


@router.get("", response_model=Page[ProjectOut])
def list_projects(
    db: DbSession,
    ctx: Ctx,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
) -> Page[ProjectOut]:
    stmt = select(Project).where(
        Project.organization_id == ctx.org_id, Project.deleted_at.is_(None)
    )
    if status_filter:
        try:
            stmt = stmt.where(Project.status == ProjectStatus(status_filter))
        except ValueError as exc:
            raise ValidationError(f"Unknown project status: {status_filter}") from exc
    if search:
        stmt = stmt.where(Project.title.ilike(f"%{search}%"))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    projects = (
        db.execute(stmt.order_by(Project.updated_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Page[ProjectOut](
        items=[ProjectOut.model_validate(project) for project in projects],
        total=int(total),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, request: Request, db: DbSession, ctx: WriteCtx) -> ProjectOut:
    verify_csrf(request)
    project = Project(
        organization_id=ctx.org_id,
        created_by_id=ctx.user.id,
        title=payload.title.strip(),
        description=payload.description,
        default_aspect_ratio=payload.default_aspect_ratio,
        default_platform=payload.default_platform,
        source_language=payload.source_language,
        brand_kit_id=payload.brand_kit_id,
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    record_audit(
        db,
        action="project.created",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="project",
        target_id=str(project.id),
        request=request,
    )
    db.commit()
    return ProjectOut.model_validate(project)


@router.get("/{project_id}", response_model=ProjectDetail)
def read_project(project_id: uuid.UUID, db: DbSession, ctx: Ctx) -> ProjectDetail:
    project = get_project(db, ctx.org_id, project_id)
    return _detail(db, project)


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> ProjectOut:
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(project, field, value)
    if project.status == ProjectStatus.ARCHIVED:
        pass
    record_audit(
        db,
        action="project.updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="project",
        target_id=str(project.id),
        metadata={"fields": sorted(data.keys())},
        request=request,
    )
    db.commit()
    return ProjectOut.model_validate(project)


@router.delete("/{project_id}", status_code=202)
def delete_project(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> dict[str, object]:
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    project.deleted_at = datetime.now(timezone.utc)
    job = enqueue(
        db,
        organization_id=project.organization_id,
        job_type=JobType.PROJECT_DELETE,
        payload={"project_id": str(project.id)},
        project_id=None,
        created_by_id=ctx.user.id,
    )
    record_audit(
        db,
        action="project.deleted",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="project",
        target_id=str(project.id),
        request=request,
    )
    db.commit()
    return {"deleted": True, "job": job_out(job).model_dump(mode="json")}


@router.get("/{project_id}/jobs", response_model=list[JobOut])
def project_jobs(project_id: uuid.UUID, db: DbSession, ctx: Ctx, active_only: bool = True) -> list[JobOut]:
    project = get_project(db, ctx.org_id, project_id)
    stmt = select(ProcessingJob).where(ProcessingJob.project_id == project.id)
    if active_only:
        stmt = stmt.where(
            ProcessingJob.status.notin_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
        )
    jobs = db.execute(stmt.order_by(ProcessingJob.created_at.desc()).limit(100)).scalars().all()
    return [job_out(job) for job in jobs]


@router.get("/{project_id}/media", response_model=list[MediaAssetOut])
def project_media(project_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[MediaAssetOut]:
    project = get_project(db, ctx.org_id, project_id)
    media = (
        db.execute(
            select(MediaAsset)
            .where(MediaAsset.project_id == project.id, MediaAsset.deleted_at.is_(None))
            .order_by(MediaAsset.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [media_out(item) for item in media]


__all__ = ["get_project", "router"]

_ = (is_active_job, ClipStatus, MediaStatus, TranscriptStatus, Clip, Export)
