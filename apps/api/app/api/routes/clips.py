"""Clip editing, caption tracks and rendering."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.config import settings
from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, ValidationError
from app.models import (
    CaptionTrack,
    Clip,
    ClipRevision,
    ClipStatus,
    Export,
    ExportKind,
    ExportStatus,
    JobStatus,
    JobType,
    MediaAsset,
    MediaStatus,
    ProcessingJob,
    Project,
    Transcript,
    TranscriptSegment,
)
from app.schemas import (
    CaptionPreview,
    CaptionTrackOut,
    CaptionTrackUpdate,
    ClipCreate,
    ClipOut,
    ClipRevisionOut,
    ClipUpdate,
    DownloadLink,
    ExportOut,
    JobOut,
    RenderRequest,
)
from app.services import captions as caption_service
from app.services.audit import record_audit
from app.services.clip_service import caption_track_for, clip_duration, create_clip, record_revision
from app.api.routes.projects import get_project
from app.api.serializers import job_out
from app.storage import get_storage
from app.workers.queue import enqueue

router = APIRouter(tags=["clips"])


def get_clip(db, org_id: uuid.UUID, clip_id: uuid.UUID) -> Clip:
    clip = db.execute(
        select(Clip).where(
            Clip.id == clip_id, Clip.organization_id == org_id, Clip.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if clip is None:
        raise NotFoundError("Clip not found", code="clip_not_found")
    return clip


def _clip_media(db, clip: Clip) -> MediaAsset:
    media = db.get(MediaAsset, clip.media_asset_id)
    if media is None:
        raise NotFoundError("Source media is missing for this clip", code="media_not_found")
    return media


def _segments_for_clip(db, clip: Clip, *, strip_fillers: bool = False) -> list[dict]:
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == clip.media_asset_id)
    ).scalar_one_or_none()
    if transcript is None:
        return []
    from app.services.highlights import strip_filler_words

    payload = []
    for segment in transcript.segments:
        text = segment.text
        if strip_fillers:
            text = strip_filler_words(text)
        if not text.strip():
            continue
        payload.append(
            {
                "id": str(segment.id),
                "start_seconds": segment.start_seconds,
                "end_seconds": segment.end_seconds,
                "text": text,
                "words": segment.words or [],
            }
        )
    return payload


def _style_for_clip(db, clip: Clip) -> tuple[CaptionTrack, dict]:
    track = caption_track_for(db, clip)
    style = caption_service.style_for_preset(track.preset, track.style)
    return track, caption_service.clamp_style(style)


@router.get("/projects/{project_id}/clips", response_model=list[ClipOut])
def list_clips(project_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[ClipOut]:
    project = get_project(db, ctx.org_id, project_id)
    clips = (
        db.execute(
            select(Clip)
            .where(Clip.project_id == project.id, Clip.deleted_at.is_(None))
            .order_by(Clip.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [ClipOut.model_validate(clip) for clip in clips]


@router.post("/projects/{project_id}/clips", response_model=ClipOut, status_code=201)
def create_manual_clip(
    project_id: uuid.UUID, payload: ClipCreate, request: Request, db: DbSession, ctx: WriteCtx
) -> ClipOut:
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
        raise ValidationError("No processed media in this project", code="no_ready_media")
    from app.models import ClipCandidate

    candidate = None
    start = payload.start_seconds
    end = payload.end_seconds
    title = payload.title
    if payload.candidate_id:
        candidate = db.execute(
            select(ClipCandidate).where(
                ClipCandidate.id == payload.candidate_id,
                ClipCandidate.organization_id == ctx.org_id,
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise NotFoundError("Candidate not found", code="candidate_not_found")
        start = candidate.start_seconds
        end = candidate.end_seconds
        title = title or candidate.suggested_title
    if start is None or end is None:
        raise ValidationError("Clip boundaries are required", code="missing_bounds")
    clip = create_clip(
        db,
        project=project,
        media=media,
        created_by_id=ctx.user.id,
        start_seconds=float(start),
        end_seconds=float(end),
        title=title or "Untitled clip",
        aspect_ratio=payload.aspect_ratio,
        target_platform=payload.target_platform,
        brand_kit_id=payload.brand_kit_id,
        candidate=candidate,
    )
    record_audit(
        db,
        action="clip.created",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip",
        target_id=str(clip.id),
        request=request,
    )
    db.commit()
    return ClipOut.model_validate(clip)


@router.get("/clips/{clip_id}", response_model=ClipOut)
def read_clip(clip_id: uuid.UUID, db: DbSession, ctx: Ctx) -> ClipOut:
    return ClipOut.model_validate(get_clip(db, ctx.org_id, clip_id))


@router.patch("/clips/{clip_id}", response_model=ClipOut)
def update_clip(
    clip_id: uuid.UUID, payload: ClipUpdate, request: Request, db: DbSession, ctx: WriteCtx
) -> ClipOut:
    verify_csrf(request)
    clip = get_clip(db, ctx.org_id, clip_id)
    media = _clip_media(db, clip)
    data = payload.model_dump(exclude_unset=True)
    note = data.pop("note", None)
    changed: list[str] = []
    if "title" in data and data["title"]:
        clip.title = data["title"][:300]
        changed.append("title")
    if "description" in data:
        clip.description = data["description"]
        changed.append("description")
    if "target_platform" in data:
        clip.target_platform = data["target_platform"]
        changed.append("target_platform")
    if "brand_kit_id" in data:
        clip.brand_kit_id = data["brand_kit_id"]
        changed.append("brand_kit")
    if "aspect_ratio" in data and data["aspect_ratio"]:
        from app.models import AspectRatio

        clip.aspect_ratio = AspectRatio(data["aspect_ratio"])
        changed.append("aspect_ratio")
    new_start = float(data.get("start_seconds", clip.start_seconds))
    new_end = float(data.get("end_seconds", clip.end_seconds))
    if new_end <= new_start:
        raise ValidationError("Clip end must be after its start", code="invalid_clip_window")
    if media.duration_seconds and new_start >= media.duration_seconds:
        raise ValidationError("Clip start is beyond the media duration", code="clip_out_of_range")
    if media.duration_seconds:
        new_end = min(new_end, media.duration_seconds)
    if new_end - new_start < 1.0:
        raise ValidationError("Clip must be at least 1 second long", code="clip_too_short")
    if new_start != clip.start_seconds or new_end != clip.end_seconds:
        clip.start_seconds = round(new_start, 3)
        clip.end_seconds = round(new_end, 3)
        changed.append("timing")
    if "settings" in data and data["settings"] is not None:
        merged = {**(clip.settings or {}), **data["settings"]}
        clip.settings = merged
        changed.append("settings")
    if not changed:
        return ClipOut.model_validate(clip)
    clip.status = ClipStatus.DRAFT
    record_revision(db, clip, created_by_id=ctx.user.id, note=note or f"Updated {', '.join(changed)}")
    record_audit(
        db,
        action="clip.updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip",
        target_id=str(clip.id),
        metadata={"fields": changed},
        request=request,
    )
    db.commit()
    return ClipOut.model_validate(clip)


@router.delete("/clips/{clip_id}", status_code=204, response_model=None)
def delete_clip(clip_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> None:
    verify_csrf(request)
    clip = get_clip(db, ctx.org_id, clip_id)
    storage = get_storage()
    for export in clip.exports:
        for key in (export.storage_key, export.thumbnail_key):
            if key:
                try:
                    storage.delete(key)
                except Exception:  # noqa: BLE001
                    pass
    clip.deleted_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action="clip.deleted",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip",
        target_id=str(clip.id),
        request=request,
    )
    db.commit()
    return None


@router.get("/clips/{clip_id}/revisions", response_model=list[ClipRevisionOut])
def list_revisions(clip_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[ClipRevisionOut]:
    clip = get_clip(db, ctx.org_id, clip_id)
    revisions = (
        db.execute(
            select(ClipRevision)
            .where(ClipRevision.clip_id == clip.id)
            .order_by(ClipRevision.revision.desc())
        )
        .scalars()
        .all()
    )
    return [ClipRevisionOut.model_validate(revision) for revision in revisions]


@router.post("/clips/{clip_id}/range-from-segments", response_model=ClipOut)
def range_from_segments(
    clip_id: uuid.UUID,
    segment_ids: list[uuid.UUID],
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> ClipOut:
    """Transcript-based editing: snap the clip window to selected transcript cues."""
    verify_csrf(request)
    clip = get_clip(db, ctx.org_id, clip_id)
    media = _clip_media(db, clip)
    if not segment_ids:
        raise ValidationError("Select at least one transcript segment", code="no_segments")
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == clip.media_asset_id)
    ).scalar_one_or_none()
    if transcript is None:
        raise ValidationError("This clip has no transcript to edit from", code="transcript_required")
    segments = (
        db.execute(
            select(TranscriptSegment).where(
                TranscriptSegment.transcript_id == transcript.id,
                TranscriptSegment.id.in_(segment_ids),
            )
        )
        .scalars()
        .all()
    )
    if not segments:
        raise ValidationError("None of the selected segments belong to this transcript", code="invalid_segments")
    start = min(segment.start_seconds for segment in segments)
    end = max(segment.end_seconds for segment in segments)
    if media.duration_seconds:
        end = min(end, media.duration_seconds)
    clip.start_seconds = round(start, 3)
    clip.end_seconds = round(end, 3)
    clip.status = ClipStatus.DRAFT
    record_revision(db, clip, created_by_id=ctx.user.id, note="Trimmed from transcript selection")
    db.commit()
    return ClipOut.model_validate(clip)


# --------------------------------------------------------------------------- captions
@router.get("/clips/{clip_id}/captions", response_model=CaptionTrackOut)
def read_captions(clip_id: uuid.UUID, db: DbSession, ctx: Ctx) -> CaptionTrackOut:
    clip = get_clip(db, ctx.org_id, clip_id)
    track, _style = _style_for_clip(db, clip)
    db.commit()
    return CaptionTrackOut.model_validate(track)


@router.patch("/clips/{clip_id}/captions", response_model=CaptionTrackOut)
def update_captions(
    clip_id: uuid.UUID,
    payload: CaptionTrackUpdate,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> CaptionTrackOut:
    verify_csrf(request)
    clip = get_clip(db, ctx.org_id, clip_id)
    track, _style = _style_for_clip(db, clip)
    if payload.enabled is not None:
        track.enabled = payload.enabled
    if payload.style is not None:
        style = payload.style.model_dump()
        track.preset = payload.style.preset
        track.style = {**track.style, **style}
    if payload.text_overrides is not None:
        track.text_overrides = {str(key): value for key, value in payload.text_overrides.items()}
    track.version += 1
    clip.status = ClipStatus.DRAFT
    record_audit(
        db,
        action="clip.captions_updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="caption_track",
        target_id=str(track.id),
        request=request,
    )
    db.commit()
    return CaptionTrackOut.model_validate(track)


@router.get("/clips/{clip_id}/captions/preview", response_model=CaptionPreview)
def preview_captions(clip_id: uuid.UUID, db: DbSession, ctx: Ctx) -> CaptionPreview:
    clip = get_clip(db, ctx.org_id, clip_id)
    track, style = _style_for_clip(db, clip)
    strip_fillers = bool((clip.settings or {}).get("remove_filler_words"))
    segments = _segments_for_clip(db, clip, strip_fillers=strip_fillers)
    lines = caption_service.preview_caption_lines(
        segments, style, clip_start=clip.start_seconds, clip_end=clip.end_seconds
    )
    warnings: list[str] = []
    if not track.enabled:
        warnings.append("Captions are currently disabled for this clip.")
    if not segments:
        warnings.append("No transcript text overlaps this clip window, so no captions will render.")
    caption_lines = caption_service.build_caption_lines(
        segments, style, clip_start=clip.start_seconds, clip_end=clip.end_seconds
    )
    warnings.extend(caption_service.estimate_reading_speed(caption_lines))
    return CaptionPreview(lines=lines, style=style, warnings=warnings)


@router.get("/clips/{clip_id}/captions/export")
def export_captions(
    clip_id: uuid.UUID,
    db: DbSession,
    ctx: Ctx,
    format: Annotated[str, Query(pattern="^(srt|vtt|ass)$")] = "srt",
) -> Response:
    clip = get_clip(db, ctx.org_id, clip_id)
    _track, style = _style_for_clip(db, clip)
    segments = _segments_for_clip(db, clip, strip_fillers=bool((clip.settings or {}).get("remove_filler_words")))
    lines = caption_service.build_caption_lines(
        segments, style, clip_start=clip.start_seconds, clip_end=clip.end_seconds
    )
    if not lines:
        raise ValidationError(
            "No captions can be produced: the transcript has no text inside this clip window.",
            code="no_caption_lines",
        )
    stem = "".join(ch for ch in clip.title if ch.isalnum() or ch in " -_").strip().replace(" ", "_")[:60] or "clip"
    if format == "srt":
        body, media_type = caption_service.build_srt(lines, style), "application/x-subrip"
    elif format == "vtt":
        body, media_type = caption_service.build_vtt(lines, style), "text/vtt"
    else:
        canvas = caption_service.canvas_for_ratio(
            clip.aspect_ratio.value, (clip.settings or {}).get("resolution", "1080p")
        )
        body = caption_service.build_ass(
            lines,
            style,
            canvas=canvas,
            duration=clip_duration(clip),
            title=clip.title,
        )
        media_type = "text/x-ssa"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'},
    )


# --------------------------------------------------------------------------- rendering
@router.post("/clips/{clip_id}/render", response_model=JobOut, status_code=202)
def render_clip_endpoint(
    clip_id: uuid.UUID,
    payload: RenderRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> JobOut:
    verify_csrf(request)
    clip = get_clip(db, ctx.org_id, clip_id)
    media = _clip_media(db, clip)
    if media.status != MediaStatus.READY:
        raise ValidationError("Source media is not ready", code="media_not_ready")
    if clip_duration(clip) < 1:
        raise ValidationError("Clip is too short to render", code="clip_too_short")
    if clip_duration(clip) > settings.max_duration_seconds:
        raise ValidationError(
            f"Clip exceeds the {settings.max_duration_seconds}s render limit", code="clip_too_long"
        )
    active = db.execute(
        select(ProcessingJob).where(
            ProcessingJob.clip_id == clip.id,
            ProcessingJob.type == JobType.CLIP_RENDER,
            ProcessingJob.status.notin_([JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]),
        )
    ).scalar_one_or_none()
    if active is not None and not payload.force:
        return job_out(active)
    overrides = payload.settings.model_dump() if payload.settings else {}
    if overrides:
        clip.settings = {**(clip.settings or {}), **overrides}
        record_revision(db, clip, created_by_id=ctx.user.id, note="Render settings changed")
    export = Export(
        organization_id=ctx.org_id,
        project_id=clip.project_id,
        clip_id=clip.id,
        kind=ExportKind.VIDEO,
        status=ExportStatus.QUEUED,
        format="mp4",
        render_settings={"requested_settings": overrides},
        created_by_id=ctx.user.id,
    )
    db.add(export)
    db.flush()
    job = enqueue(
        db,
        organization_id=ctx.org_id,
        job_type=JobType.CLIP_RENDER,
        payload={"clip_id": str(clip.id), "export_id": str(export.id), "settings": overrides},
        project_id=clip.project_id,
        clip_id=clip.id,
        export_id=export.id,
        media_asset_id=clip.media_asset_id,
        created_by_id=ctx.user.id,
    )
    record_audit(
        db,
        action="clip.render_requested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="clip",
        target_id=str(clip.id),
        metadata={"export_id": str(export.id)},
        request=request,
    )
    db.commit()
    return job_out(job)


@router.get("/clips/{clip_id}/exports", response_model=list[ExportOut])
def clip_exports(clip_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[ExportOut]:
    clip = get_clip(db, ctx.org_id, clip_id)
    exports = (
        db.execute(
            select(Export).where(Export.clip_id == clip.id).order_by(Export.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [ExportOut.model_validate(export) for export in exports]


@router.get("/projects/{project_id}/exports", response_model=list[ExportOut])
def project_exports(project_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[ExportOut]:
    project = get_project(db, ctx.org_id, project_id)
    exports = (
        db.execute(
            select(Export)
            .where(Export.project_id == project.id)
            .order_by(Export.created_at.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    return [ExportOut.model_validate(export) for export in exports]


@router.get("/exports/{export_id}", response_model=ExportOut)
def read_export(export_id: uuid.UUID, db: DbSession, ctx: Ctx) -> ExportOut:
    export = db.execute(
        select(Export).where(Export.id == export_id, Export.organization_id == ctx.org_id)
    ).scalar_one_or_none()
    if export is None:
        raise NotFoundError("Export not found", code="export_not_found")
    return ExportOut.model_validate(export)


@router.get("/exports/{export_id}/download", response_model=DownloadLink)
def download_export(export_id: uuid.UUID, db: DbSession, ctx: Ctx) -> DownloadLink:
    export = db.execute(
        select(Export).where(Export.id == export_id, Export.organization_id == ctx.org_id)
    ).scalar_one_or_none()
    if export is None:
        raise NotFoundError("Export not found", code="export_not_found")
    if export.status != ExportStatus.READY or not export.storage_key:
        raise ValidationError("This export is not ready to download yet", code="export_not_ready")
    export.download_count += 1
    db.commit()
    url = get_storage().signed_url(export.storage_key, ttl_seconds=settings.signed_url_ttl_seconds)
    clip = db.get(Clip, export.clip_id) if export.clip_id else None
    stem = "clip"
    if clip is not None:
        stem = "".join(ch for ch in clip.title if ch.isalnum() or ch in " -_").strip().replace(" ", "_")[:60] or "clip"
    suffix = "srt" if export.kind == ExportKind.SUBTITLES else "mp4"
    import time as _time

    return DownloadLink(
        url=url,
        expires_at=int(_time.time()) + settings.signed_url_ttl_seconds,
        filename=f"{stem}.{suffix}",
    )


@router.delete("/exports/{export_id}", status_code=204, response_model=None)
def delete_export(export_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> None:
    verify_csrf(request)
    export = db.execute(
        select(Export).where(Export.id == export_id, Export.organization_id == ctx.org_id)
    ).scalar_one_or_none()
    if export is None:
        raise NotFoundError("Export not found", code="export_not_found")
    storage = get_storage()
    for key in (export.storage_key, export.thumbnail_key):
        if key:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001
                pass
    db.delete(export)
    db.commit()
    return None


_ = (Project, CaptionTrackOut, ExportStatus)
