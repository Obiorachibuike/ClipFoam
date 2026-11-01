"""Timestamped transcript retrieval, editing and import."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Request
from sqlalchemy import delete, select

from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, UnsupportedMedia, ValidationError
from app.models import MediaAsset, MediaStatus, Transcript, TranscriptSegment, TranscriptStatus
from app.schemas import (
    TranscriptImportRequest,
    TranscriptOut,
    TranscriptSegmentOut,
    TranscriptSegmentUpdate,
)
from app.services.audit import record_audit
from app.services.captions import format_timestamp
from app.services.transcription import parse_subtitles

router = APIRouter(prefix="/transcripts", tags=["transcripts"])


class TranscriptImportBody(TranscriptImportRequest):
    pass


def get_transcript(db, org_id: uuid.UUID, transcript_id: uuid.UUID) -> Transcript:
    transcript = db.execute(
        select(Transcript).where(
            Transcript.id == transcript_id, Transcript.organization_id == org_id
        )
    ).scalar_one_or_none()
    if transcript is None:
        raise NotFoundError("Transcript not found", code="transcript_not_found")
    return transcript


@router.get("/{transcript_id}", response_model=TranscriptOut)
def read_transcript(transcript_id: uuid.UUID, db: DbSession, ctx: Ctx) -> TranscriptOut:
    transcript = get_transcript(db, ctx.org_id, transcript_id)
    return TranscriptOut.model_validate(transcript)


@router.post("/import", response_model=TranscriptOut, status_code=201)
def import_transcript(
    media_id: uuid.UUID,
    payload: TranscriptImportRequest,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> TranscriptOut:
    """Attach a real transcript the user already has (SRT/VTT/JSON)."""
    verify_csrf(request)
    media = db.execute(
        select(MediaAsset).where(
            MediaAsset.id == media_id,
            MediaAsset.organization_id == ctx.org_id,
            MediaAsset.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if media is None:
        raise NotFoundError("Media not found", code="media_not_found")
    if media.status not in {MediaStatus.READY, MediaStatus.PROCESSING}:
        raise ValidationError("Media is not ready for a transcript import", code="media_not_ready")
    try:
        segments = parse_subtitles(payload.content, payload.format)
    except UnsupportedMedia as exc:
        raise UnsupportedMedia(exc.message, code=exc.code) from exc
    if not segments:
        raise ValidationError("No cues found in the supplied transcript", code="empty_transcript")
    if media.duration_seconds:
        overshoot = [seg for seg in segments if seg.start > media.duration_seconds + 5]
        if overshoot:
            raise ValidationError(
                f"Transcript contains {len(overshoot)} cue(s) beyond the media duration "
                f"({media.duration_seconds:.0f}s). Check that it belongs to this file.",
                code="transcript_out_of_range",
            )

    transcript = db.execute(
        select(Transcript).where(Transcript.media_asset_id == media.id)
    ).scalar_one_or_none()
    if transcript is None:
        transcript = Transcript(
            organization_id=ctx.org_id,
            media_asset_id=media.id,
            provider="manual_upload",
        )
        db.add(transcript)
        db.flush()
    else:
        db.execute(delete(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id))
        db.flush()
    for segment in segments:
        db.add(
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
    transcript.is_manual = True
    transcript.provider = "manual_upload"
    transcript.model = None
    transcript.language = payload.language
    transcript.duration_seconds = media.duration_seconds
    transcript.word_count = sum(len(seg.text.split()) for seg in segments)
    transcript.error_message = None
    transcript.raw_payload = {"import_format": payload.format, "is_manual": True}
    record_audit(
        db,
        action="transcript.imported",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="transcript",
        target_id=str(transcript.id),
        metadata={"format": payload.format, "segments": len(segments)},
        request=request,
    )
    db.commit()
    db.refresh(transcript)
    return TranscriptOut.model_validate(transcript)


@router.patch("/{transcript_id}/segments/{segment_id}", response_model=TranscriptSegmentOut)
def update_segment(
    transcript_id: uuid.UUID,
    segment_id: uuid.UUID,
    payload: TranscriptSegmentUpdate,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> TranscriptSegmentOut:
    verify_csrf(request)
    transcript = get_transcript(db, ctx.org_id, transcript_id)
    segment = db.execute(
        select(TranscriptSegment).where(
            TranscriptSegment.id == segment_id, TranscriptSegment.transcript_id == transcript.id
        )
    ).scalar_one_or_none()
    if segment is None:
        raise NotFoundError("Transcript segment not found", code="segment_not_found")
    data = payload.model_dump(exclude_unset=True)
    if "start_seconds" in data and data["start_seconds"] is not None:
        segment.start_seconds = max(0.0, float(data["start_seconds"]))
    if "end_seconds" in data and data["end_seconds"] is not None:
        segment.end_seconds = float(data["end_seconds"])
    if segment.end_seconds <= segment.start_seconds:
        raise ValidationError("Segment end must be after its start", code="invalid_segment_window")
    if "text" in data and data["text"] is not None:
        cleaned = str(data["text"]).strip()
        if not cleaned:
            raise ValidationError("Segment text cannot be empty", code="empty_segment_text")
        segment.text = cleaned
        segment.is_edited = segment.text != segment.original_text
        segment.words = []
    if "speaker" in data:
        segment.speaker = data["speaker"]
    transcript.word_count = sum(len(item.text.split()) for item in transcript.segments)
    record_audit(
        db,
        action="transcript.segment_updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="transcript_segment",
        target_id=str(segment.id),
        metadata={"fields": sorted(data.keys())},
        request=request,
    )
    db.commit()
    return TranscriptSegmentOut.model_validate(segment)


@router.post("/{transcript_id}/segments", response_model=TranscriptSegmentOut, status_code=201)
def insert_segment(
    transcript_id: uuid.UUID,
    payload: TranscriptSegmentUpdate,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> TranscriptSegmentOut:
    verify_csrf(request)
    transcript = get_transcript(db, ctx.org_id, transcript_id)
    if payload.text is None or payload.start_seconds is None or payload.end_seconds is None:
        raise ValidationError(
            "text, start_seconds and end_seconds are required to insert a segment",
            code="incomplete_segment",
        )
    segment = TranscriptSegment(
        transcript_id=transcript.id,
        index=(max([item.index for item in transcript.segments], default=-1) + 1),
        start_seconds=payload.start_seconds,
        end_seconds=payload.end_seconds,
        text=payload.text.strip(),
        original_text="",
        words=[],
        speaker=payload.speaker,
    )
    db.add(segment)
    db.flush()
    transcript.segments.sort(key=lambda item: (item.start_seconds, item.index))
    for index, item in enumerate(transcript.segments):
        item.index = index
    transcript.word_count = sum(len(item.text.split()) for item in transcript.segments)
    record_audit(
        db,
        action="transcript.segment_created",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="transcript_segment",
        target_id=str(segment.id),
        request=request,
    )
    db.commit()
    return TranscriptSegmentOut.model_validate(segment)


@router.delete("/{transcript_id}/segments/{segment_id}", status_code=204, response_model=None)
def delete_segment(
    transcript_id: uuid.UUID,
    segment_id: uuid.UUID,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
) -> None:
    verify_csrf(request)
    transcript = get_transcript(db, ctx.org_id, transcript_id)
    segment = db.execute(
        select(TranscriptSegment).where(
            TranscriptSegment.id == segment_id, TranscriptSegment.transcript_id == transcript.id
        )
    ).scalar_one_or_none()
    if segment is None:
        raise NotFoundError("Transcript segment not found", code="segment_not_found")
    db.delete(segment)
    record_audit(
        db,
        action="transcript.segment_deleted",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="transcript_segment",
        target_id=str(segment_id),
        request=request,
    )
    db.commit()
    return None


@router.get("/{transcript_id}/cues")
def list_cues(transcript_id: uuid.UUID, db: DbSession, ctx: Ctx) -> list[dict[str, object]]:
    transcript = get_transcript(db, ctx.org_id, transcript_id)
    return [
        {
            "id": str(segment.id),
            "index": segment.index,
            "start": segment.start_seconds,
            "end": segment.end_seconds,
            "start_label": format_timestamp(segment.start_seconds),
            "end_label": format_timestamp(segment.end_seconds),
            "text": segment.text,
            "is_edited": segment.is_edited,
        }
        for segment in transcript.segments
    ]


_ = Annotated
