"""Job handlers: the real work behind every background task.

Each handler is idempotent with respect to its inputs: re-running a render or a
transcription overwrites deterministic outputs instead of duplicating rows, and
publishing is guarded by an idempotency key at the queue level plus a status
check at the handler level.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import AppError, ProviderUnavailable, UnsupportedMedia
from app.models import (
    BrandKit,
    CaptionTrack,
    Clip,
    ClipCandidate,
    ClipRevision,
    ClipStatus,
    CandidateAlgorithm,
    CandidateStatus,
    ContentItem,
    Export,
    ExportKind,
    ExportStatus,
    JobStatus,
    JobType,
    MediaAsset,
    MediaStatus,
    ProcessingJob,
    Project,
    PublishAttemptStatus,
    PublishingAttempt,
    ScheduledPost,
    ScheduledPostStatus,
    SocialAccount,
    SocialAccountStatus,
    SocialPost,
    SocialPostStatus,
    SourceKind,
    Transcript,
    TranscriptSegment,
    TranscriptStatus,
    UsageKind,
    VisualPreset,
)
from app.models import SocialAccount as _SocialAccount  # noqa: F401  (typing clarity)
from app.services import captions as caption_service
from app.services.audit import record_usage
from app.services.highlights import HighlightOptions, ai_candidates, detect_highlights, merge_candidates
from app.services.importer import ImportFailed, ImportRejected, download_to_file
from app.services.media import MediaError, generate_thumbnail, probe_media
from app.services.publishing import PublishError, PublishPayload, get_publisher
from app.services.render import (
    RenderInputs,
    analyze_face_crop,
    build_caption_document,
    make_silence_ranges,
    music_path_for,
    plan_render,
    render_clip,
    resolve_canvas,
    validate_music_choice,
)
from app.services.transcription import (
    SegmentData,
    get_transcription_provider,
    segments_to_json,
)
from app.storage import StorageError, build_key, get_storage, temp_dir
from app.workers.queue import utcnow

logger = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


class PermanentJobError(Exception):
    def __init__(self, message: str, *, code: str = "permanent_error"):
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class JobContext:
    db: Session
    job: ProcessingJob
    worker_id: str

    def stage(self, status: JobStatus, message: str, progress: int) -> None:
        self.job.status = status
        self.job.stage_message = message[:300]
        self.job.progress = max(0, min(99, progress))
        self.job.heartbeat_at = utcnow()
        self.db.commit()

    def check_cancelled(self) -> None:
        self.db.refresh(self.job)
        if self.job.cancellation_requested:
            raise JobCancelled()


# --------------------------------------------------------------------------- helpers
def _transcript_segments(db: Session, transcript_id: uuid.UUID) -> list[TranscriptSegment]:
    return list(
        db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcript_id == transcript_id)
            .order_by(TranscriptSegment.index)
        )
        .scalars()
        .all()
    )


def segment_payload(segment: TranscriptSegment, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    text = segment.text
    if overrides and str(segment.id) in overrides:
        text = str(overrides[str(segment.id)])
    words = segment.words or []
    if overrides and str(segment.id) in overrides and words:
        # Edited text invalidates word-level timings; fall back to even distribution.
        words = []
    return {
        "id": str(segment.id),
        "index": segment.index,
        "start_seconds": segment.start_seconds,
        "end_seconds": segment.end_seconds,
        "text": text,
        "words": words,
    }


def _latest_transcript(db: Session, media_id: uuid.UUID) -> Transcript | None:
    return db.execute(
        select(Transcript).where(Transcript.media_asset_id == media_id)
    ).scalar_one_or_none()


def _ingest_media(ctx: JobContext, media: MediaAsset) -> dict[str, Any]:
    storage = get_storage()
    ctx.stage(JobStatus.VALIDATING, "Validating media", 8)
    try:
        local_path = storage.open_local(media.storage_key)
    except StorageError as exc:
        media.status = MediaStatus.FAILED
        media.error_message = str(exc)
        ctx.db.commit()
        raise PermanentJobError(f"Stored media is missing: {exc}", code="storage_missing") from exc

    try:
        media.status = MediaStatus.PROCESSING
        ctx.db.commit()
        ctx.stage(JobStatus.INGESTING, "Reading media metadata", 20)
        info = probe_media(local_path)

        if info.duration_seconds > settings.max_duration_seconds:
            raise PermanentJobError(
                f"Media is {info.duration_seconds / 60:.1f} minutes long; the configured limit is "
                f"{settings.max_duration_seconds / 60:.0f} minutes.",
                code="duration_limit_exceeded",
            )
        if info.duration_seconds < settings.min_duration_seconds:
            raise PermanentJobError("Media is too short to process", code="duration_too_short")
        if media.size_bytes and media.size_bytes > settings.max_upload_mb * 1024 * 1024:
            raise PermanentJobError(
                f"File exceeds the {settings.max_upload_mb} MB upload limit",
                code="file_too_large",
            )

        media.duration_seconds = info.duration_seconds
        media.width = info.width
        media.height = info.height
        media.fps = info.fps
        media.video_codec = info.video_codec
        media.audio_codec = info.audio_codec
        media.has_audio = info.has_audio
        media.probe_data = {
            "source": info.probe_source,
            "format_name": info.format_name,
            "bit_rate": info.bit_rate,
        }
        if info.size_bytes:
            media.size_bytes = int(info.size_bytes)

        ctx.stage(JobStatus.GENERATING_PREVIEWS, "Generating thumbnail", 60)
        workdir = temp_dir("ingest")
        try:
            thumb_path = workdir / "thumb.jpg"
            generate_thumbnail(
                local_path,
                thumb_path,
                at_seconds=min(1.5, max(0.0, info.duration_seconds / 3)),
            )
            thumb_key = build_key("thumbs", str(media.organization_id), f"{media.id}.jpg")
            storage.save_bytes(thumb_path.read_bytes(), thumb_key, "image/jpeg")
            media.thumbnail_key = thumb_key
        except MediaError as exc:
            logger.warning("Thumbnail generation failed for %s: %s", media.id, exc)
            media.probe_data = {**media.probe_data, "thumbnail_error": str(exc)}
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        media.status = MediaStatus.READY
        media.error_message = None
        if media.kind.value == "video" and (media.width or 0) == 0:
            media.kind = media.kind
        record_usage(
            ctx.db,
            organization_id=media.organization_id,
            kind=UsageKind.UPLOAD_BYTES,
            quantity=float(media.size_bytes),
            unit="bytes",
            project_id=media.project_id,
            job_id=ctx.job.id,
        )
        if media.project_id:
            project = ctx.db.get(Project, media.project_id)
            if project is not None and project.status.value == "draft":
                from app.models import ProjectStatus

                project.status = ProjectStatus.ACTIVE
        ctx.db.commit()
        ctx.stage(JobStatus.STORING_OUTPUTS, "Finalising media record", 90)
        return {
            "media_asset_id": str(media.id),
            "duration_seconds": media.duration_seconds,
            "width": media.width,
            "height": media.height,
            "probe_source": info.probe_source,
        }
    except (PermanentJobError, JobCancelled):
        raise
    except MediaError as exc:
        media.status = MediaStatus.FAILED
        media.error_message = str(exc)
        ctx.db.commit()
        raise PermanentJobError(str(exc), code="media_unreadable") from exc
    finally:
        storage.release_local(local_path)


# --------------------------------------------------------------------------- handlers
def handle_media_ingest(ctx: JobContext) -> dict[str, Any]:
    media_id = uuid.UUID(str(ctx.job.payload.get("media_asset_id") or ctx.job.media_asset_id))
    media = ctx.db.get(MediaAsset, media_id)
    if media is None:
        raise PermanentJobError("Media asset not found", code="media_not_found")
    return _ingest_media(ctx, media)


def handle_media_import(ctx: JobContext) -> dict[str, Any]:
    media_id = uuid.UUID(str(ctx.job.payload.get("media_asset_id") or ctx.job.media_asset_id))
    url = str(ctx.job.payload.get("url") or "")
    media = ctx.db.get(MediaAsset, media_id)
    if media is None:
        raise PermanentJobError("Media asset not found", code="media_not_found")
    ctx.stage(JobStatus.VALIDATING, "Checking import URL", 5)
    workdir = temp_dir("import")
    storage = get_storage()
    try:
        ctx.check_cancelled()
        destination = workdir / f"source{media.extension or '.mp4'}"
        target = destination
        try:
            result = download_to_file(url, target)
        except ImportRejected as exc:
            media.status = MediaStatus.FAILED
            media.error_message = str(exc)
            ctx.db.commit()
            raise PermanentJobError(str(exc), code="import_rejected") from exc
        except ImportFailed as exc:
            raise ProviderUnavailable(str(exc), code="import_failed") from exc
        ctx.check_cancelled()
        ctx.stage(JobStatus.INGESTING, f"Storing {result.size_bytes // 1024} KB download", 35)
        with result.path.open("rb") as handle:
            size = storage.save_stream(handle, media.storage_key, media.mime_type)
        media.size_bytes = size
        media.source_url = url
        media.source_kind = SourceKind.URL_IMPORT
        ctx.db.commit()
        return _ingest_media(ctx, media)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _transcription_cache_key(media: MediaAsset, language: str | None) -> str:
    provider = settings.transcription_provider
    model = settings.whisper_model if provider == "faster_whisper" else "manual"
    digest = hashlib.sha256(
        f"{media.checksum_sha256 or media.storage_key}|{provider}|{model}|{language or 'auto'}".encode()
    ).hexdigest()
    return f"{provider}:{model}:{digest[:48]}"


def handle_media_transcribe(ctx: JobContext) -> dict[str, Any]:
    media_id = uuid.UUID(str(ctx.job.payload.get("media_asset_id") or ctx.job.media_asset_id))
    force = bool(ctx.job.payload.get("force"))
    language = ctx.job.payload.get("language")
    media = ctx.db.get(MediaAsset, media_id)
    if media is None:
        raise PermanentJobError("Media asset not found", code="media_not_found")
    if media.status != MediaStatus.READY:
        raise PermanentJobError(
            f"Media is not ready for transcription (status: {media.status.value})", code="media_not_ready"
        )
    if not media.has_audio:
        raise PermanentJobError("This media has no audio track to transcribe", code="no_audio_track")

    cache_key = _transcription_cache_key(media, language)
    transcript = _latest_transcript(ctx.db, media.id)
    if transcript and transcript.status == TranscriptStatus.READY and not force:
        return {
            "transcript_id": str(transcript.id),
            "cached": True,
            "segments": len(transcript.segments),
            "message": "Transcript already exists; reused instead of re-transcribing.",
        }
    if transcript is None:
        transcript = Transcript(
            organization_id=media.organization_id,
            media_asset_id=media.id,
            status=TranscriptStatus.PENDING,
            provider=settings.transcription_provider,
        )
        ctx.db.add(transcript)
        ctx.db.flush()
    transcript.status = TranscriptStatus.PENDING
    transcript.error_message = None
    transcript.cache_key = cache_key
    ctx.db.commit()

    # Reuse an identical transcript already produced inside this workspace.
    if settings.transcription_cache and not force:
        shared = (
            ctx.db.execute(
                select(Transcript)
                .where(
                    Transcript.organization_id == media.organization_id,
                    Transcript.cache_key == cache_key,
                    Transcript.status == TranscriptStatus.READY,
                    Transcript.media_asset_id != media.id,
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if shared is not None and shared.segments:
            for segment in transcript.segments:
                ctx.db.delete(segment)
            ctx.db.flush()
            for segment in shared.segments:
                ctx.db.add(
                    TranscriptSegment(
                        transcript_id=transcript.id,
                        index=segment.index,
                        start_seconds=segment.start_seconds,
                        end_seconds=segment.end_seconds,
                        text=segment.text,
                        original_text=segment.original_text,
                        words=segment.words,
                        speaker=segment.speaker,
                    )
                )
            transcript.status = TranscriptStatus.READY
            transcript.language = shared.language
            transcript.provider = shared.provider
            transcript.model = shared.model
            transcript.word_count = shared.word_count
            transcript.duration_seconds = shared.duration_seconds
            transcript.raw_payload = {"reused_from": str(shared.id)}
            ctx.db.commit()
            return {
                "transcript_id": str(transcript.id),
                "cached": True,
                "segments": shared.word_count,
                "message": "Reused a cached transcript for identical media.",
            }

    ctx.stage(JobStatus.TRANSCRIBING, "Preparing audio for transcription", 15)
    storage = get_storage()
    workdir = temp_dir("transcribe")
    try:
        source = storage.open_local(media.storage_key)
        try:
            from app.services.media import extract_audio

            audio_path = extract_audio(source, workdir / "audio.wav")
        finally:
            storage.release_local(source)
        ctx.check_cancelled()
        ctx.stage(JobStatus.TRANSCRIBING, "Transcribing speech", 35)
        provider = get_transcription_provider()
        result = provider.transcribe(audio_path, language=language)
        ctx.check_cancelled()
        ctx.stage(JobStatus.STORING_OUTPUTS, "Saving transcript", 85)

        existing_segments = _transcript_segments(ctx.db, transcript.id)
        if existing_segments:
            ctx.db.execute(delete(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id))
            ctx.db.flush()
        for segment in result.segments:
            ctx.db.add(
                TranscriptSegment(
                    transcript_id=transcript.id,
                    index=segment.index,
                    start_seconds=segment.start,
                    end_seconds=segment.end,
                    text=segment.text,
                    original_text=segment.text,
                    words=segment.words,
                )
            )
        transcript.status = TranscriptStatus.READY
        transcript.language = result.language
        transcript.provider = result.provider
        transcript.model = result.model
        transcript.duration_seconds = result.duration_seconds or media.duration_seconds
        transcript.word_count = sum(len(seg.text.split()) for seg in result.segments)
        transcript.error_message = None
        transcript.raw_payload = {**result.raw, "is_manual": False}
        record_usage(
            ctx.db,
            organization_id=media.organization_id,
            kind=UsageKind.TRANSCRIPTION_SECONDS,
            quantity=float(media.duration_seconds or 0),
            unit="seconds",
            project_id=media.project_id,
            job_id=ctx.job.id,
            provider=result.provider,
            metadata={"model": result.model},
        )
        ctx.db.commit()
        return {
            "transcript_id": str(transcript.id),
            "cached": False,
            "segments": len(result.segments),
            "language": result.language,
            "provider": result.provider,
            "model": result.model,
        }
    except (ProviderUnavailable, UnsupportedMedia) as exc:
        transcript.status = TranscriptStatus.FAILED
        transcript.error_message = exc.message
        ctx.db.commit()
        raise PermanentJobError(exc.message, code=getattr(exc, "code", "transcription_failed")) from exc
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def handle_generate_candidates(ctx: JobContext) -> dict[str, Any]:
    payload = ctx.job.payload
    project_id = uuid.UUID(str(payload.get("project_id") or ctx.job.project_id))
    project = ctx.db.get(Project, project_id)
    if project is None:
        raise PermanentJobError("Project not found", code="project_not_found")
    media_ids = [str(item) for item in payload.get("media_asset_ids") or []]
    media = None
    if media_ids:
        media = ctx.db.get(MediaAsset, uuid.UUID(media_ids[0]))
    if media is None:
        media = (
            ctx.db.execute(
                select(MediaAsset)
                .where(MediaAsset.project_id == project.id, MediaAsset.status == MediaStatus.READY)
                .order_by(MediaAsset.created_at.desc())
            )
            .scalars()
            .first()
        )
    if media is None:
        raise PermanentJobError("No processed media is available in this project", code="no_media")

    transcript = _latest_transcript(ctx.db, media.id)
    if transcript is None or transcript.status != TranscriptStatus.READY:
        raise PermanentJobError(
            "A completed transcript is required before highlights can be detected.",
            code="transcript_required",
        )
    segments = [segment_payload(segment) for segment in _transcript_segments(ctx.db, transcript.id)]
    if not segments:
        raise PermanentJobError("Transcript has no segments", code="transcript_empty")

    ctx.stage(JobStatus.ANALYZING, "Scoring transcript windows", 25)
    options = HighlightOptions(
        clip_count=int(payload.get("clip_count", settings.default_clip_count)),
        min_duration=float(payload.get("min_duration", settings.min_clip_seconds)),
        max_duration=float(payload.get("max_duration", settings.max_clip_seconds)),
        pause_threshold=settings.candidate_pause_threshold,
        target_duration=min(
            float(payload.get("max_duration", settings.max_clip_seconds)) * 0.7,
            max(float(payload.get("min_duration", settings.min_clip_seconds)) * 1.4, 30.0),
        ),
    )
    windows = detect_highlights(
        segments, duration=media.duration_seconds or 0, options=options
    )
    ai_meta: dict[str, Any] = {}
    ai_result: list[Any] = []
    if payload.get("use_ai"):
        from app.services.ai import get_text_provider

        ctx.stage(JobStatus.ANALYZING, "Requesting model analysis", 45)
        try:
            provider = get_text_provider(payload.get("ai_provider"))
            ai_result, ai_meta = ai_candidates(
                provider,
                segments,
                duration=media.duration_seconds or 0,
                options=options,
                language=payload.get("language"),
            )
            if ai_meta.get("error"):
                ai_meta["fallback"] = "deterministic candidates were used"
        except AppError as exc:
            ai_meta = {"error": exc.message, "fallback": "deterministic candidates were used"}
        except Exception as exc:  # noqa: BLE001 - provider integrations fail in many ways
            logger.warning("AI candidate generation failed: %s", exc)
            ai_meta = {"error": str(exc), "fallback": "deterministic candidates were used"}

    selected = merge_candidates(windows, ai_result, options.clip_count) if ai_result else windows
    if not selected:
        raise PermanentJobError(
            "No candidate windows could be built. The transcript may be too short for the "
            f"requested {options.min_duration:.0f}-{options.max_duration:.0f}s clip length.",
            code="no_candidates",
        )
    ctx.check_cancelled()
    ctx.stage(JobStatus.STORING_OUTPUTS, "Saving candidates", 85)

    for old in (
        ctx.db.execute(
            select(ClipCandidate).where(
                ClipCandidate.project_id == project.id,
                ClipCandidate.status == CandidateStatus.PENDING,
            )
        )
        .scalars()
        .all()
    ):
        old.status = CandidateStatus.SUPERSEDED

    created: list[ClipCandidate] = []
    for rank, candidate in enumerate(selected, start=1):
        validated = (
            candidate.start >= 0
            and candidate.end > candidate.start
            and (candidate.end - candidate.start) >= options.min_duration * 0.85
            and (candidate.end - candidate.start) <= options.max_duration * 1.15
            and (media.duration_seconds is None or candidate.end <= media.duration_seconds + 0.5)
        )
        record = ClipCandidate(
            organization_id=project.organization_id,
            project_id=project.id,
            transcript_id=transcript.id,
            rank=rank,
            start_seconds=candidate.start,
            end_seconds=candidate.end,
            excerpt=candidate.excerpt[:8000],
            suggested_title=candidate.title,
            hook=candidate.hook,
            category=candidate.category,
            score=candidate.score,
            score_breakdown=candidate.breakdown,
            explanation=candidate.explanation,
            algorithm=CandidateAlgorithm(candidate.algorithm),
            model=candidate.model,
            status=CandidateStatus.PENDING,
            validation_status="valid" if validated else "rejected",
            validation_notes=None if validated else "Window fell outside the allowed duration range",
            metadata_json={
                "word_count": candidate.word_count,
                "speech_seconds": candidate.speech_seconds,
                "mode": candidate.algorithm,
            },
        )
        ctx.db.add(record)
        created.append(record)
    if ai_meta:
        ctx.job.result = {"ai": ai_meta}
    ctx.db.commit()
    return {
        "candidates": len(created),
        "candidate_ids": [str(item.id) for item in created],
        "ai": ai_meta or None,
        "algorithm": "ai_assisted" if any(c.algorithm == CandidateAlgorithm.AI_ASSISTED for c in created) else "deterministic",
    }


def _caption_style_for(ctx: JobContext, clip: Clip, brand_kit: BrandKit | None) -> dict[str, Any]:
    track = clip.caption_track
    base_preset = track.preset if track else (brand_kit.caption_preset if brand_kit else VisualPreset.BOLD_CREATOR)
    overrides: dict[str, Any] = {}
    if brand_kit and brand_kit.caption_style:
        overrides.update({k: v for k, v in brand_kit.caption_style.items() if v is not None})
        overrides.setdefault("text_color", brand_kit.text_color)
        overrides.setdefault("highlight_color", brand_kit.highlight_color)
        overrides.setdefault("font_family", brand_kit.font_family)
    if track and track.style:
        overrides.update({k: v for k, v in track.style.items() if v is not None})
    style = caption_service.style_for_preset(base_preset, overrides)
    if track is None or not track.enabled:
        style = {**style}
    return caption_service.clamp_style(style)


def handle_clip_render(ctx: JobContext) -> dict[str, Any]:
    payload = ctx.job.payload
    clip_id = uuid.UUID(str(payload.get("clip_id") or ctx.job.clip_id))
    clip = ctx.db.get(Clip, clip_id)
    if clip is None:
        raise PermanentJobError("Clip not found", code="clip_not_found")
    media = ctx.db.get(MediaAsset, clip.media_asset_id)
    if media is None or media.status != MediaStatus.READY:
        raise PermanentJobError("Source media is not ready", code="media_not_ready")

    settings_override = payload.get("settings") or {}
    clip_settings = {**(clip.settings or {}), **settings_override}
    resolution = clip_settings.get("resolution", "1080p")
    canvas = resolve_canvas(clip.aspect_ratio.value, resolution)

    export = None
    if ctx.job.export_id:
        export = ctx.db.get(Export, ctx.job.export_id)
    if export is None:
        export = Export(
            organization_id=clip.organization_id,
            project_id=clip.project_id,
            clip_id=clip.id,
            kind=ExportKind.VIDEO,
            status=ExportStatus.RENDERING,
            format="mp4",
            created_by_id=ctx.job.created_by_id,
        )
        ctx.db.add(export)
        ctx.db.flush()
        ctx.job.export_id = export.id
    export.status = ExportStatus.RENDERING
    export.error_message = None
    clip.status = ClipStatus.RENDERING
    ctx.db.commit()

    ctx.stage(JobStatus.RENDERING, "Preparing render", 10)
    workdir = temp_dir("render")
    storage = get_storage()
    source_path = None
    try:
        source_path = storage.open_local(media.storage_key)
        info = probe_media(source_path)
        transcript = _latest_transcript(ctx.db, media.id)
        track = clip.caption_track
        brand_kit = None
        if clip.brand_kit_id:
            brand_kit = ctx.db.get(BrandKit, clip.brand_kit_id)
        elif clip.project.brand_kit_id:
            brand_kit = ctx.db.get(BrandKit, clip.project.brand_kit_id)

        style = _caption_style_for(ctx.db, clip, brand_kit)  # type: ignore[arg-type]
        lines: list[Any] = []
        captions_enabled = bool(clip_settings.get("captions_enabled", True)) and (track is None or track.enabled)
        if captions_enabled and transcript is not None:
            segments = [
                segment_payload(segment, track.text_overrides if track else None)
                for segment in _transcript_segments(ctx.db, transcript.id)
            ]
            lines = caption_service.build_caption_lines(
                segments, style, clip_start=clip.start_seconds, clip_end=clip.end_seconds
            )
        if captions_enabled and not lines:
            ctx.job.result = {**(ctx.job.result or {}), "captions": "no transcript segments in range"}

        plan = plan_render(
            media_info=info,
            clip_start=clip.start_seconds,
            clip_end=clip.end_seconds,
            aspect_ratio=clip.aspect_ratio.value,
            clip_settings={**clip_settings, "captions_enabled": bool(lines)},
            caption_lines=lines,
        )

        music_choice = clip_settings.get("background_music", "none")
        music_warning = validate_music_choice(music_choice)
        if music_warning:
            raise PermanentJobError(music_warning, code="music_unavailable")

        keep_ranges = None
        if clip_settings.get("remove_silence"):
            ctx.stage(JobStatus.RENDERING, "Detecting silences", 20)
            keep_ranges = make_silence_ranges(
                source_path,
                clip_start=plan.clip_start,
                clip_end=plan.clip_end,
                min_silence=float(clip_settings.get("silence_min_seconds", 0.6)),
            )
            plan.keep_ranges = keep_ranges
            plan.duration = round(sum(end - start for start, end in keep_ranges), 3)

        face_crop = None
        if clip_settings.get("face_tracking") or clip_settings.get("reframe_mode") == "face":
            ctx.stage(JobStatus.RENDERING, "Analysing subject framing", 25)
            face_crop = analyze_face_crop(
                source_path,
                start=plan.clip_start,
                end=plan.clip_end,
                target_ratio=canvas[0] / canvas[1],
            )
            if face_crop is None:
                plan.warnings.append("Subject detection failed; centre crop applied.")

        ass_path = None
        if lines or clip_settings.get("overlay_text") or clip_settings.get("intro_text") or clip_settings.get("outro_text"):
            document = build_caption_document(
                lines=lines,
                style=style,
                canvas=canvas,
                duration=plan.duration,
                overlay_text=clip_settings.get("overlay_text"),
                overlay_position=clip_settings.get("overlay_position", "top"),
                intro_text=clip_settings.get("intro_text") or (brand_kit.intro_text if brand_kit else None),
                outro_text=clip_settings.get("outro_text") or (brand_kit.outro_text if brand_kit else None),
            )
            ass_path = workdir / "captions.ass"
            ass_path.write_text(document, encoding="utf-8")

        logo_path = None
        if clip_settings.get("logo_enabled") and brand_kit and brand_kit.logo_storage_key:
            try:
                logo_source = storage.open_local(brand_kit.logo_storage_key)
                logo_path = workdir / "logo.png"
                shutil.copyfile(logo_source, logo_path)
                storage.release_local(logo_source)
            except StorageError:
                plan.warnings.append("Brand logo could not be loaded; rendering without it.")

        music_path = music_path_for(music_choice)
        ctx.check_cancelled()
        ctx.stage(JobStatus.RENDERING, "Encoding video", 40)
        output_path = workdir / "output.mp4"
        result = render_clip(
            RenderInputs(
                source_path=source_path,
                output_path=output_path,
                work_dir=workdir,
                plan=plan,
                captions_path=ass_path,
                logo_path=logo_path,
                music_path=music_path,
                has_audio=info.has_audio,
                face_crop=face_crop,
                keep_ranges=keep_ranges,
            )
        )
        ctx.check_cancelled()
        ctx.stage(JobStatus.STORING_OUTPUTS, "Uploading export", 80)

        export_key = build_key(
            "orgs", str(clip.organization_id), "exports", str(export.id), "clip.mp4"
        )
        with output_path.open("rb") as handle:
            size = storage.save_stream(handle, export_key, "video/mp4")
        thumb_key = None
        try:
            thumb_file = workdir / "poster.jpg"
            generate_thumbnail(output_path, thumb_file, at_seconds=min(1.0, plan.duration / 2))
            thumb_key = build_key("orgs", str(clip.organization_id), "exports", str(export.id), "poster.jpg")
            storage.save_bytes(thumb_file.read_bytes(), thumb_key, "image/jpeg")
        except MediaError as exc:
            plan.warnings.append(f"Poster generation failed: {exc}")

        subtitle_key = None
        if lines:
            srt = caption_service.build_srt(lines, style)
            subtitle_key = build_key(
                "orgs", str(clip.organization_id), "exports", str(export.id), "captions.srt"
            )
            storage.save_bytes(srt.encode("utf-8"), subtitle_key, "application/x-subrip")
            vtt = caption_service.build_vtt(lines, style)
            vtt_key = build_key("orgs", str(clip.organization_id), "exports", str(export.id), "captions.vtt")
            storage.save_bytes(vtt.encode("utf-8"), vtt_key, "text/vtt")
            export_subtitle = Export(
                organization_id=clip.organization_id,
                project_id=clip.project_id,
                clip_id=clip.id,
                kind=ExportKind.SUBTITLES,
                status=ExportStatus.READY,
                format="srt",
                storage_key=subtitle_key,
                size_bytes=len(srt.encode("utf-8")),
                render_settings={"vtt_key": vtt_key, "style": style},
                completed_at=utcnow(),
                created_by_id=ctx.job.created_by_id,
            )
            ctx.db.add(export_subtitle)

        export.status = ExportStatus.READY
        export.storage_key = export_key
        export.thumbnail_key = thumb_key
        export.size_bytes = size
        export.duration_seconds = result["duration_seconds"]
        export.width = result["width"]
        export.height = result["height"]
        export.fps = result["fps"]
        export.render_settings = {
            **plan.to_dict(),
            "caption_style": style,
            "captions_rendered": bool(lines),
            "subtitle_key": subtitle_key,
            "face_crop": list(face_crop) if face_crop else None,
            "logo": bool(logo_path),
            "music": music_choice,
            "quality": plan.quality,
            "resolution": resolution,
        }
        export.completed_at = utcnow()
        clip.status = ClipStatus.READY
        record_usage(
            ctx.db,
            organization_id=clip.organization_id,
            kind=UsageKind.RENDER_SECONDS,
            quantity=float(plan.duration),
            unit="seconds",
            project_id=clip.project_id,
            job_id=ctx.job.id,
        )
        ctx.db.commit()
        return {
            "export_id": str(export.id),
            "size_bytes": size,
            "duration_seconds": result["duration_seconds"],
            "width": result["width"],
            "height": result["height"],
            "warnings": plan.warnings,
            "captions": len(lines),
        }
    except JobCancelled:
        export.status = ExportStatus.QUEUED
        clip.status = ClipStatus.DRAFT
        ctx.db.commit()
        raise
    except (MediaError, ValueError) as exc:
        export.status = ExportStatus.FAILED
        export.error_message = str(exc)
        clip.status = ClipStatus.FAILED
        ctx.db.commit()
        raise PermanentJobError(str(exc), code="render_failed") from exc
    except Exception as exc:  # noqa: BLE001
        export.status = ExportStatus.FAILED
        export.error_message = str(exc)[:2000]
        clip.status = ClipStatus.FAILED
        ctx.db.commit()
        raise
    finally:
        if source_path is not None:
            storage.release_local(source_path)
        shutil.rmtree(workdir, ignore_errors=True)


def handle_social_publish(ctx: JobContext) -> dict[str, Any]:
    payload = ctx.job.payload
    post_id = uuid.UUID(str(payload.get("social_post_id") or ctx.job.social_post_id))
    post = ctx.db.get(SocialPost, post_id)
    if post is None:
        raise PermanentJobError("Post not found", code="post_not_found")
    if post.status == SocialPostStatus.PUBLISHED:
        return {"skipped": True, "reason": "already published", "external_id": post.published_external_id}

    account = None
    if payload.get("social_account_id"):
        account = ctx.db.get(SocialAccount, uuid.UUID(str(payload["social_account_id"])))
    elif post.social_account_id:
        account = ctx.db.get(SocialAccount, post.social_account_id)
    if account is None:
        account = (
            ctx.db.execute(
                select(SocialAccount).where(
                    SocialAccount.organization_id == post.organization_id,
                    SocialAccount.platform == post.platform,
                    SocialAccount.status == SocialAccountStatus.CONNECTED,
                )
            )
            .scalars()
            .first()
        )
    if account is None:
        post.status = SocialPostStatus.FAILED
        post.error_message = f"No connected {post.platform.value} account. Connect one or download the export."
        # Record the failure as an attempt so the UI can explain why nothing posted.
        ctx.db.add(
            PublishingAttempt(
                organization_id=post.organization_id,
                social_post_id=post.id,
                social_account_id=None,
                attempt=max(1, ctx.job.attempts),
                status=PublishAttemptStatus.FAILED,
                error_code="no_social_account",
                error_message=post.error_message,
                started_at=utcnow(),
                finished_at=utcnow(),
            )
        )
        ctx.db.commit()
        raise PermanentJobError(post.error_message, code="no_social_account")

    from app.security import decrypt_secret

    try:
        access_token = decrypt_secret(account.access_token_encrypted)
    except ValueError as exc:
        ctx.db.add(
            PublishingAttempt(
                organization_id=post.organization_id,
                social_post_id=post.id,
                social_account_id=account.id,
                attempt=max(1, ctx.job.attempts),
                status=PublishAttemptStatus.FAILED,
                error_code="token_unreadable",
                error_message=str(exc)[:500],
                started_at=utcnow(),
                finished_at=utcnow(),
            )
        )
        post.status = SocialPostStatus.FAILED
        post.error_message = "Stored credentials could not be decrypted. Reconnect the account."
        ctx.db.commit()
        raise PermanentJobError(str(exc), code="token_unreadable") from exc

    publisher = get_publisher(post.platform.value)
    ctx.stage(JobStatus.STORING_OUTPUTS, f"Publishing to {post.platform.value}", 30)

    # Duplicate-publish protection: a previous attempt may have succeeded remotely.
    prior = (
        ctx.db.execute(
            select(PublishingAttempt).where(
                PublishingAttempt.social_post_id == post.id,
                PublishingAttempt.status == PublishAttemptStatus.SUCCEEDED,
            )
        )
        .scalars()
        .first()
    )
    if prior is not None:
        post.status = SocialPostStatus.PUBLISHED
        post.published_external_id = (prior.response_payload or {}).get("external_id")
        ctx.db.commit()
        return {"skipped": True, "reason": "existing successful attempt re-used", "external_id": post.published_external_id}

    attempt_number = ctx.job.attempts
    attempt = PublishingAttempt(
        organization_id=post.organization_id,
        social_post_id=post.id,
        social_account_id=account.id,
        attempt=attempt_number,
        status=PublishAttemptStatus.STARTED,
        started_at=utcnow(),
    )
    ctx.db.add(attempt)
    post.status = SocialPostStatus.PUBLISHING
    post.social_account_id = account.id
    ctx.db.commit()

    media_path = None
    storage = get_storage()
    workdir = temp_dir("publish")
    try:
        export = ctx.db.get(Export, post.export_id) if post.export_id else None
        if export is not None and export.storage_key:
            media_path = storage.open_local(export.storage_key)
        elif post.platform.value == "youtube":
            raise PublishError(
                "Publish to YouTube requires a rendered MP4 export. Render the clip first.",
                code="missing_export",
            )
        result = publisher.publish(
            access_token=access_token,
            payload=PublishPayload(
                title=post.title or (post.clip.title if post.clip else "ClipFoam clip"),
                body=post.body,
                hashtags=[str(tag) for tag in (post.hashtags or [])],
                media_path=media_path,
                platform_metadata=post.platform_metadata or {},
            ),
        )
        post.status = SocialPostStatus.PUBLISHED
        post.published_external_id = result.external_id
        post.published_url = result.url
        post.published_at = utcnow()
        post.error_message = None
        attempt.status = PublishAttemptStatus.SUCCEEDED
        attempt.response_payload = {"external_id": result.external_id, "url": result.url, **result.raw}
        attempt.finished_at = utcnow()
        record_usage(
            ctx.db,
            organization_id=post.organization_id,
            kind=UsageKind.PUBLISH_ATTEMPT,
            quantity=1,
            unit="count",
            project_id=post.project_id,
            job_id=ctx.job.id,
            provider=post.platform.value,
        )
        scheduled = ctx.db.execute(
            select(ScheduledPost).where(ScheduledPost.social_post_id == post.id)
        ).scalar_one_or_none()
        if scheduled is not None:
            scheduled.status = ScheduledPostStatus.DONE
        ctx.db.commit()
        return {
            "published": True,
            "external_id": result.external_id,
            "url": result.url,
        }
    except PublishError as exc:
        attempt.status = PublishAttemptStatus.FAILED
        attempt.error_code = exc.code
        attempt.error_message = exc.message[:2000]
        attempt.response_payload = exc.raw
        attempt.finished_at = utcnow()
        if exc.code in {"token_expired", "token_invalid"}:
            account.status = SocialAccountStatus.EXPIRED
        if exc.retryable and ctx.job.attempts < ctx.job.max_attempts:
            post.status = SocialPostStatus.SCHEDULED if post.scheduled_at else SocialPostStatus.DRAFT
            ctx.db.commit()
            raise ProviderUnavailable(exc.message, code=exc.code) from exc
        post.status = SocialPostStatus.FAILED
        post.error_message = exc.message[:2000]
        scheduled = ctx.db.execute(
            select(ScheduledPost).where(ScheduledPost.social_post_id == post.id)
        ).scalar_one_or_none()
        if scheduled is not None:
            scheduled.status = ScheduledPostStatus.FAILED
            scheduled.error_message = exc.message[:500]
        ctx.db.commit()
        raise PermanentJobError(exc.message, code=exc.code) from exc
    finally:
        if media_path is not None:
            storage.release_local(media_path)
        shutil.rmtree(workdir, ignore_errors=True)


def handle_project_delete(ctx: JobContext) -> dict[str, Any]:
    project_id = uuid.UUID(str(ctx.job.payload.get("project_id") or ctx.job.project_id))
    project = ctx.db.get(Project, project_id)
    if project is None:
        return {"deleted": False, "reason": "already deleted"}
    storage = get_storage()
    removed = 0
    for media in project.media_assets:
        for key in (media.storage_key, media.thumbnail_key):
            if key:
                try:
                    storage.delete(key)
                    removed += 1
                except Exception as exc:  # noqa: BLE001 - best effort cleanup
                    logger.warning("Failed to delete object %s: %s", key, exc)
    for clip in project.clips:
        for export in clip.exports:
            for key in (export.storage_key, export.thumbnail_key):
                if key:
                    try:
                        storage.delete(key)
                        removed += 1
                    except Exception:  # noqa: BLE001
                        pass
                extra = (export.render_settings or {}).get("subtitle_key")
                if extra:
                    try:
                        storage.delete(extra)
                        removed += 1
                    except Exception:  # noqa: BLE001
                        pass
    project.deleted_at = utcnow()
    ctx.db.delete(project)
    ctx.db.commit()
    return {"deleted": True, "objects_removed": removed}


def handle_account_delete(ctx: JobContext) -> dict[str, Any]:
    from app.models import Organization, User

    user_id = ctx.job.payload.get("user_id")
    organization_id = ctx.job.payload.get("organization_id")
    storage = get_storage()
    projects = (
        ctx.db.execute(select(Project).where(Project.organization_id == uuid.UUID(str(organization_id))))
        .scalars()
        .all()
    )
    for project in projects:
        for media in project.media_assets:
            for key in (media.storage_key, media.thumbnail_key):
                if key:
                    try:
                        storage.delete(key)
                    except Exception:  # noqa: BLE001
                        pass
        for clip in project.clips:
            for export in clip.exports:
                if export.storage_key:
                    try:
                        storage.delete(export.storage_key)
                    except Exception:  # noqa: BLE001
                        pass
    if organization_id:
        organization = ctx.db.get(Organization, uuid.UUID(str(organization_id)))
        if organization is not None:
            ctx.db.delete(organization)
    if user_id:
        user = ctx.db.get(User, uuid.UUID(str(user_id)))
        if user is not None:
            ctx.db.delete(user)
    ctx.db.commit()
    return {"deleted": True}


def handle_maintenance_cleanup(ctx: JobContext) -> dict[str, Any]:
    from datetime import timedelta

    ctx.stage(JobStatus.VALIDATING, "Scanning temporary directories", 20)
    removed = 0
    cutoff = utcnow() - timedelta(hours=settings.temp_cleanup_hours)
    root = Path(settings.temp_work_path)
    if root.exists():
        for child in root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff.timestamp():
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    ctx.stage(JobStatus.STORING_OUTPUTS, "Pruning retention-expired projects", 60)
    retention_cutoff = utcnow() - timedelta(days=settings.retention_days)
    expired = (
        ctx.db.execute(
            select(Project).where(
                Project.deleted_at.is_not(None), Project.deleted_at < retention_cutoff
            )
        )
        .scalars()
        .all()
    )
    for project in expired:
        ctx.db.delete(project)
    ctx.db.commit()
    return {"temp_dirs_removed": removed, "projects_purged": len(expired)}


HANDLERS: dict[JobType, Callable[[JobContext], dict[str, Any]]] = {
    JobType.MEDIA_INGEST: handle_media_ingest,
    JobType.MEDIA_IMPORT: handle_media_import,
    JobType.MEDIA_TRANSCRIBE: handle_media_transcribe,
    JobType.PROJECT_GENERATE_CANDIDATES: handle_generate_candidates,
    JobType.CLIP_RENDER: handle_clip_render,
    JobType.SOCIAL_PUBLISH: handle_social_publish,
    JobType.PROJECT_DELETE: handle_project_delete,
    JobType.ACCOUNT_DELETE: handle_account_delete,
    JobType.MAINTENANCE_CLEANUP: handle_maintenance_cleanup,
}


def dispatch(ctx: JobContext) -> dict[str, Any]:
    handler = HANDLERS.get(ctx.job.type)
    if handler is None:
        raise PermanentJobError(f"No handler registered for job type {ctx.job.type}", code="unknown_job")
    return handler(ctx)


_ = (CaptionTrack, ClipCandidate, ClipRevision, ContentItem, SegmentData, segments_to_json, VisualPreset, SourceKind, JobStatus)
