"""Media ingestion: uploads, authorized URL imports, transcription triggers."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select

from app.config import settings
from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, QuotaExceeded, UnsupportedMedia, ValidationError
from app.models import (
    JobType,
    MediaAsset,
    MediaKind,
    MediaStatus,
    SourceKind,
    Transcript,
    UsageKind,
    UsageRecord,
)
from app.schemas import (
    JobOut,
    MediaAssetOut,
    TranscribeRequest,
    TranscriptOut,
    UploadResponse,
    UrlImportRequest,
)
from app.services.audit import record_audit, record_usage
from app.services.file_validation import read_head, validate_upload
from app.api.routes.projects import get_project
from app.api.serializers import job_out, media_out
from app.services.importer import ImportRejected, validate_import_url
from app.storage import build_key, get_storage, temp_dir
from app.workers.queue import enqueue

router = APIRouter(prefix="/media", tags=["media"])

CHUNK = 1024 * 1024


def get_media(db, org_id: uuid.UUID, media_id: uuid.UUID) -> MediaAsset:
    media = db.execute(
        select(MediaAsset).where(
            MediaAsset.id == media_id,
            MediaAsset.organization_id == org_id,
            MediaAsset.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if media is None:
        raise NotFoundError("Media not found", code="media_not_found")
    return media


def _storage_used_bytes(db, org_id: uuid.UUID) -> int:
    total = db.execute(
        select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(
            MediaAsset.organization_id == org_id, MediaAsset.deleted_at.is_(None)
        )
    ).scalar_one()
    exports = db.execute(
        select(func.coalesce(func.sum(UsageRecord.quantity), 0)).where(
            UsageRecord.organization_id == org_id, UsageRecord.kind == UsageKind.UPLOAD_BYTES
        )
    ).scalar_one()
    return int(total) + int(exports)


def _assert_quota(db, org_id: uuid.UUID, incoming: int = 0) -> None:
    used = _storage_used_bytes(db, org_id)
    quota = settings.storage_quota_mb * 1024 * 1024
    if used + incoming > quota:
        raise QuotaExceeded(
            f"Workspace storage quota of {settings.storage_quota_mb // 1024} GB reached "
            f"({used // (1024 * 1024)} MB used). Delete media or raise CF_STORAGE_QUOTA_MB.",
            code="storage_quota_exceeded",
        )


@router.post("/projects/{project_id}/upload", response_model=UploadResponse, status_code=202)
async def upload_media(
    project_id: uuid.UUID,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
    file: Annotated[UploadFile, File()],
    kind_hint: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    _ = kind_hint
    max_bytes = settings.max_upload_mb * 1024 * 1024
    workdir = temp_dir("upload")
    temp_path = workdir / "incoming.bin"
    digest = hashlib.sha256()
    size = 0
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UnsupportedMedia(
                        f"File exceeds the {settings.max_upload_mb} MB upload limit",
                        code="file_too_large",
                    )
                digest.update(chunk)
                handle.write(chunk)
        validated = validate_upload(
            filename=file.filename or "upload",
            declared_mime=file.content_type,
            head=read_head(temp_path),
            size_bytes=size,
            checksum=digest.hexdigest(),
        )
        _assert_quota(db, ctx.org_id, validated.size_bytes)

        media_id = uuid.uuid4()
        storage_key = build_key(
            "orgs", str(ctx.org_id), "media", str(media_id), f"source.{validated.extension}"
        )
        with temp_path.open("rb") as handle:
            stored = get_storage().save_stream(handle, storage_key, validated.mime_type)

        media = MediaAsset(
            id=media_id,
            organization_id=ctx.org_id,
            project_id=project.id,
            uploaded_by_id=ctx.user.id,
            kind=MediaKind.VIDEO if validated.kind == "video" else MediaKind.AUDIO,
            status=MediaStatus.PROCESSING,
            original_filename=validated.filename,
            storage_key=storage_key,
            mime_type=validated.mime_type,
            extension=validated.extension,
            size_bytes=stored,
            source_kind=SourceKind.UPLOAD,
            checksum_sha256=validated.checksum_sha256,
        )
        db.add(media)
        db.flush()
        job = enqueue(
            db,
            organization_id=ctx.org_id,
            job_type=JobType.MEDIA_INGEST,
            payload={"media_asset_id": str(media.id)},
            project_id=project.id,
            media_asset_id=media.id,
            created_by_id=ctx.user.id,
        )
        record_audit(
            db,
            action="media.uploaded",
            organization_id=ctx.org_id,
            actor_user_id=ctx.user.id,
            target_type="media",
            target_id=str(media.id),
            metadata={"size_bytes": stored, "filename": validated.filename},
            request=request,
        )
        db.commit()
        response_payload = media_out(media)
        response_payload.thumbnail_url = None
        return UploadResponse(media=response_payload, job=job_out(job))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@router.post("/projects/{project_id}/import", response_model=MediaAssetOut, status_code=202)
def import_media(
    project_id: uuid.UUID,
    payload: UrlImportRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> MediaAssetOut:
    verify_csrf(request)
    project = get_project(db, ctx.org_id, project_id)
    if not settings.allow_url_import:
        raise ValidationError("URL import is disabled for this deployment", code="import_disabled")
    try:
        url = validate_import_url(payload.url)
    except ImportRejected as exc:
        raise ValidationError(str(exc), code="import_url_rejected") from exc
    _assert_quota(db, ctx.org_id)

    from pathlib import PurePosixPath

    suffix = PurePosixPath(url.split("?")[0]).suffix.lower().lstrip(".") or "mp4"
    if suffix not in settings.video_extensions and suffix not in settings.audio_extensions:
        raise UnsupportedMedia(
            f"Cannot infer a supported media type from the URL ('{suffix}'). "
            f"Supported: {', '.join(sorted(settings.video_extensions | settings.audio_extensions))}.",
            code="unsupported_import_extension",
        )
    media_id = uuid.uuid4()
    storage_key = build_key("orgs", str(ctx.org_id), "media", str(media_id), f"source.{suffix}")
    media = MediaAsset(
        id=media_id,
        organization_id=ctx.org_id,
        project_id=project.id,
        uploaded_by_id=ctx.user.id,
        kind=MediaKind.VIDEO if suffix in settings.video_extensions else MediaKind.AUDIO,
        status=MediaStatus.PROCESSING,
        original_filename=f"import_{media_id.hex[:8]}.{suffix}",
        storage_key=storage_key,
        mime_type="video/mp4" if suffix in settings.video_extensions else "audio/mpeg",
        extension=suffix,
        size_bytes=0,
        source_kind=SourceKind.URL_IMPORT,
        source_url=url,
    )
    db.add(media)
    db.flush()
    enqueue(
        db,
        organization_id=ctx.org_id,
        job_type=JobType.MEDIA_IMPORT,
        payload={"media_asset_id": str(media.id), "url": url},
        project_id=project.id,
        media_asset_id=media.id,
        created_by_id=ctx.user.id,
    )
    record_audit(
        db,
        action="media.import_requested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="media",
        target_id=str(media.id),
        metadata={"host": url.split("/")[2] if "/" in url else url},
        request=request,
    )
    db.commit()
    return media_out(media)


@router.get("/{media_id}", response_model=MediaAssetOut)
def read_media(media_id: uuid.UUID, db: DbSession, ctx: Ctx) -> MediaAssetOut:
    return media_out(get_media(db, ctx.org_id, media_id))


@router.delete("/{media_id}", status_code=204, response_model=None)
def delete_media(media_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> None:
    verify_csrf(request)
    media = get_media(db, ctx.org_id, media_id)
    storage = get_storage()
    for key in (media.storage_key, media.thumbnail_key):
        if key:
            try:
                storage.delete(key)
            except Exception:  # noqa: BLE001 - cleanup is best effort
                pass
    media.deleted_at = datetime.now(timezone.utc)
    media.status = MediaStatus.DELETED
    record_audit(
        db,
        action="media.deleted",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="media",
        target_id=str(media.id),
        request=request,
    )
    db.commit()
    return None


@router.post("/{media_id}/transcribe", response_model=JobOut, status_code=202)
def start_transcription(
    media_id: uuid.UUID,
    payload: TranscribeRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> JobOut:
    verify_csrf(request)
    media = get_media(db, ctx.org_id, media_id)
    if media.status != MediaStatus.READY:
        raise ValidationError("Media must finish processing before transcription", code="media_not_ready")
    if not media.has_audio:
        raise ValidationError("This media has no audio track", code="no_audio_track")
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == media.id)
    ).scalar_one_or_none()
    job = enqueue(
        db,
        organization_id=ctx.org_id,
        job_type=JobType.MEDIA_TRANSCRIBE,
        payload={
            "media_asset_id": str(media.id),
            "language": payload.language,
            "force": payload.force,
        },
        project_id=media.project_id,
        media_asset_id=media.id,
        created_by_id=ctx.user.id,
        idempotency_key=None if payload.force else f"transcribe:{media.id}",
    )
    _ = transcript
    record_audit(
        db,
        action="transcription.requested",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="media",
        target_id=str(media.id),
        request=request,
    )
    db.commit()
    return job_out(job)


@router.get("/{media_id}/transcript", response_model=TranscriptOut | None)
def read_transcript(media_id: uuid.UUID, db: DbSession, ctx: Ctx) -> TranscriptOut | None:
    media = get_media(db, ctx.org_id, media_id)
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == media.id)
    ).scalar_one_or_none()
    if transcript is None:
        return None
    return TranscriptOut.model_validate(transcript)


@router.get("/{media_id}/transcript/export")
def export_transcript(
    media_id: uuid.UUID,
    db: DbSession,
    ctx: Ctx,
    format: Annotated[str, Query(pattern="^(srt|vtt|json)$")] = "srt",
):
    from fastapi import Response

    from app.services.captions import transcript_to_srt, transcript_to_vtt
    from app.services.transcription import segments_to_json

    media = get_media(db, ctx.org_id, media_id)
    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == media.id)
    ).scalar_one_or_none()
    if transcript is None:
        raise NotFoundError("No transcript exists for this media", code="transcript_not_found")
    segments = [
        {
            "start_seconds": segment.start_seconds,
            "end_seconds": segment.end_seconds,
            "text": segment.text,
            "words": segment.words,
        }
        for segment in transcript.segments
    ]
    stem = (media.original_filename or "transcript").rsplit(".", 1)[0]
    if format == "srt":
        body, media_type = transcript_to_srt(segments), "application/x-subrip"
    elif format == "vtt":
        body, media_type = transcript_to_vtt(segments), "text/vtt"
    else:
        body, media_type = (
            segments_to_json(segments, language=transcript.language, provider=transcript.provider),
            "application/json",
        )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'},
    )


_ = (UsageKind, record_usage)
