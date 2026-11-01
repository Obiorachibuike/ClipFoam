"""Clip candidate generation, review, editing and approval."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, ValidationError
from app.models import (
    Clip,
    ClipCandidate,
    CandidateStatus,
    MediaAsset,
    MediaStatus,
    JobType,
    Project,
    Transcript,
    TranscriptStatus,
)
from app.schemas import (
    CandidateGenerateResponse,
    CandidateGenerationRequest,
    CandidateApproveRequest,
    CandidateOut,
    CandidateUpdate,
    ClipOut,
)
from app.services.audit import record_audit
from app.services.clip_service import create_clip
from app.api.routes.projects import get_project
from app.api.serializers import job_out
from app.workers.queue import enqueue

router = APIRouter(tags=["candidates"])


def get_candidate(db, org_id: uuid.UUID, candidate_id: uuid.UUID) -> ClipCandidate:
    candidate = db.execute(
        select(ClipCandidate).where(
            ClipCandidate.id == candidate_id, ClipCandidate.organization_id == org_id
        )
    ).scalar_one_or_none()
    if candidate is None:
        raise NotFoundError("Candidate not found", code="candidate_not_found")
    return candidate


@router.post("/projects/{project_id}/candidates/generate", response_model=CandidateGenerateResponse, status_code=202)
def generate_candidates(
    project_id: uuid.UUID,
    payload: CandidateGenerationRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> CandidateGenerateResponse:
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    media = (
        db.execute(
            select(MediaAsset)
            .where(MediaAsset.project_id == project.id, MediaAsset.status == MediaStatus.READY)
            .order_by(MediaAsset.created_at.desc())
        )
        .scalars()
        .first()
    )
    if media is None:
        raise ValidationError(
            "Upload and process a video before generating highlights", code="no_ready_media"
        )
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == media.id)
    ).scalar_one_or_none()
    if transcript is None or transcript.status != TranscriptStatus.READY:
        raise ValidationError(
            "A completed transcript is required. Transcribe the video or import an SRT/VTT first.",
            code="transcript_required",
        )
    if media.duration_seconds and payload.min_duration > media.duration_seconds:
        raise ValidationError(
            "Minimum clip duration is longer than the source video", code="clip_longer_than_source"
        )
    job = enqueue(
        db,
        organization_id=ctx.org_id,
        job_type=JobType.PROJECT_GENERATE_CANDIDATES,
        payload={
            "project_id": str(project.id),
            "media_asset_ids": [str(media.id)],
            "clip_count": payload.clip_count,
            "min_duration": payload.min_duration,
            "max_duration": payload.max_duration,
            "target_platform": payload.target_platform,
            "use_ai": payload.use_ai,
            "language": payload.language,
        },
        project_id=project.id,
        media_asset_id=media.id,
        created_by_id=ctx.user.id,
    )
    record_audit(
        db,
        action="candidates.generation_requested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="project",
        target_id=str(project.id),
        metadata={"use_ai": payload.use_ai, "clip_count": payload.clip_count},
        request=request,
    )
    db.commit()
    return CandidateGenerateResponse(job=job_out(job))


@router.get("/projects/{project_id}/candidates", response_model=list[CandidateOut])
def list_candidates(
    project_id: uuid.UUID,
    db: DbSession,
    ctx: Ctx,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[CandidateOut]:
    project = get_project(db, ctx.org_id, project_id)
    stmt = select(ClipCandidate).where(ClipCandidate.project_id == project.id)
    if status_filter:
        try:
            stmt = stmt.where(ClipCandidate.status == CandidateStatus(status_filter))
        except ValueError as exc:
            raise ValidationError(f"Unknown candidate status: {status_filter}") from exc
    else:
        stmt = stmt.where(ClipCandidate.status != CandidateStatus.SUPERSEDED)
    candidates = (
        db.execute(stmt.order_by(ClipCandidate.rank.asc(), ClipCandidate.score.desc()).limit(limit))
        .scalars()
        .all()
    )
    return [CandidateOut.model_validate(candidate) for candidate in candidates]


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def read_candidate(candidate_id: uuid.UUID, db: DbSession, ctx: Ctx) -> CandidateOut:
    return CandidateOut.model_validate(get_candidate(db, ctx.org_id, candidate_id))


@router.patch("/candidates/{candidate_id}", response_model=CandidateOut)
def update_candidate(
    candidate_id: uuid.UUID,
    payload: CandidateUpdate,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> CandidateOut:
    verify_csrf(request)
    candidate = get_candidate(db, ctx.org_id, candidate_id)
    data = payload.model_dump(exclude_unset=True)
    start = float(data.get("start_seconds", candidate.start_seconds))
    end = float(data.get("end_seconds", candidate.end_seconds))
    if end <= start:
        raise ValidationError("Candidate end must be after its start", code="invalid_candidate_window")
    project = db.get(Project, candidate.project_id)
    media = (
        db.execute(
            select(MediaAsset)
            .where(MediaAsset.project_id == candidate.project_id, MediaAsset.status == MediaStatus.READY)
            .order_by(MediaAsset.created_at.desc())
        )
        .scalars()
        .first()
    )
    if media and media.duration_seconds and end > media.duration_seconds + 0.5:
        raise ValidationError("Candidate exceeds the media duration", code="candidate_out_of_range")
    candidate.start_seconds = start
    candidate.end_seconds = end
    if "suggested_title" in data:
        candidate.suggested_title = data["suggested_title"]
    if "hook" in data:
        candidate.hook = data["hook"]
    if "excerpt" in data:
        candidate.excerpt = data["excerpt"]
    if "category" in data:
        candidate.category = data["category"]
    if "status" in data and data["status"] is not None:
        candidate.status = CandidateStatus(data["status"])
    candidate.validation_status = "valid" if (end - start) >= 5 else "short"
    candidate.explanation = (candidate.explanation or "") + " (edited by user)"
    if project is not None:
        candidate.metadata_json = {**candidate.metadata_json, "edited": True}
    record_audit(
        db,
        action="candidate.updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip_candidate",
        target_id=str(candidate.id),
        metadata={"fields": sorted(data.keys())},
        request=request,
    )
    db.commit()
    return CandidateOut.model_validate(candidate)


@router.post("/candidates/{candidate_id}/approve", response_model=ClipOut, status_code=201)
def approve_candidate(
    candidate_id: uuid.UUID,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
    payload: CandidateApproveRequest | None = None,
) -> ClipOut:
    verify_csrf(request)
    candidate = get_candidate(db, ctx.org_id, candidate_id)
    project = db.get(Project, candidate.project_id)
    if project is None:
        raise NotFoundError("Project not found", code="project_not_found")
    media = (
        db.execute(
            select(MediaAsset)
            .where(MediaAsset.project_id == project.id, MediaAsset.status == MediaStatus.READY)
            .order_by(MediaAsset.created_at.desc())
        )
        .scalars()
        .first()
    )
    if media is None:
        raise ValidationError("No processed media for this candidate", code="no_ready_media")
    existing = db.execute(
        select(Clip).where(Clip.candidate_id == candidate.id, Clip.deleted_at.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        return ClipOut.model_validate(existing)
    clip = create_clip(
        db,
        project=project,
        media=media,
        created_by_id=ctx.user.id,
        start_seconds=candidate.start_seconds,
        end_seconds=candidate.end_seconds,
        title=(payload.title if payload and payload.title else candidate.suggested_title) or "Untitled clip",
        aspect_ratio=(payload.aspect_ratio if payload else None),
        target_platform=(payload.target_platform if payload else None),
        candidate=candidate,
        description=candidate.hook,
    )
    record_audit(
        db,
        action="candidate.approved",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip",
        target_id=str(clip.id),
        request=request,
    )
    db.commit()
    return ClipOut.model_validate(clip)


@router.post("/candidates/{candidate_id}/reject", response_model=CandidateOut)
def reject_candidate(
    candidate_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx
) -> CandidateOut:
    verify_csrf(request)
    candidate = get_candidate(db, ctx.org_id, candidate_id)
    candidate.status = CandidateStatus.REJECTED
    record_audit(
        db,
        action="candidate.rejected",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip_candidate",
        target_id=str(candidate.id),
        request=request,
    )
    db.commit()
    return CandidateOut.model_validate(candidate)


@router.post("/projects/{project_id}/candidates/rerank", response_model=list[CandidateOut])
def rerank_candidates(
    project_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx
) -> list[CandidateOut]:
    """Recompute ranks from current scores after manual edits."""
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    candidates = (
        db.execute(
            select(ClipCandidate).where(
                ClipCandidate.project_id == project.id,
                ClipCandidate.status != CandidateStatus.SUPERSEDED,
            )
        )
        .scalars()
        .all()
    )
    candidates.sort(key=lambda item: item.score, reverse=True)
    for index, candidate in enumerate(candidates, start=1):
        candidate.rank = index
    record_audit(
        db,
        action="candidates.reranked",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="project",
        target_id=str(project.id),
        request=request,
    )
    db.commit()
    return [CandidateOut.model_validate(candidate) for candidate in candidates]
