"""Clip creation, revision history and caption-track helpers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ValidationError
from app.models import (
    AspectRatio,
    BrandKit,
    CandidateStatus,
    CaptionTrack,
    Clip,
    ClipCandidate,
    ClipRevision,
    ClipStatus,
    MediaAsset,
    Project,
    VisualPreset,
)
from app.schemas import ClipSettings
from app.services.captions import style_for_preset

DEFAULT_CLIP_SETTINGS: dict[str, Any] = ClipSettings().model_dump()


def _default_style(brand_kit: BrandKit | None) -> dict[str, Any]:
    if brand_kit is None:
        return style_for_preset(VisualPreset.BOLD_CREATOR)
    overrides = dict(brand_kit.caption_style or {})
    overrides.setdefault("text_color", brand_kit.text_color)
    overrides.setdefault("highlight_color", brand_kit.highlight_color)
    overrides.setdefault("font_family", brand_kit.font_family)
    return style_for_preset(brand_kit.caption_preset, overrides)


def create_clip(
    db: Session,
    *,
    project: Project,
    media: MediaAsset,
    created_by_id: uuid.UUID | None,
    start_seconds: float,
    end_seconds: float,
    title: str,
    aspect_ratio: AspectRatio | None = None,
    target_platform: str | None = None,
    brand_kit_id: uuid.UUID | None = None,
    candidate: ClipCandidate | None = None,
    description: str | None = None,
    settings_overrides: dict[str, Any] | None = None,
) -> Clip:
    if media.status.value != "ready":
        raise ValidationError("Source media is not ready", code="media_not_ready")
    if media.duration_seconds is not None:
        if start_seconds < 0 or end_seconds <= start_seconds:
            raise ValidationError("Invalid clip window", code="invalid_clip_window")
        if start_seconds >= media.duration_seconds:
            raise ValidationError("Clip start is beyond the media duration", code="clip_out_of_range")
        end_seconds = min(end_seconds, media.duration_seconds)
        if end_seconds - start_seconds < 1.0:
            raise ValidationError("Clip must be at least 1 second long", code="clip_too_short")

    resolved_brand = brand_kit_id or project.brand_kit_id
    brand_kit = db.get(BrandKit, resolved_brand) if resolved_brand else None
    settings = {**DEFAULT_CLIP_SETTINGS, **(settings_overrides or {})}
    preset = brand_kit.caption_preset if brand_kit else VisualPreset.BOLD_CREATOR

    clip = Clip(
        organization_id=project.organization_id,
        project_id=project.id,
        media_asset_id=media.id,
        candidate_id=candidate.id if candidate else None,
        created_by_id=created_by_id,
        brand_kit_id=resolved_brand,
        title=(title or "Untitled clip")[:300],
        description=description,
        status=ClipStatus.DRAFT,
        start_seconds=round(start_seconds, 3),
        end_seconds=round(end_seconds, 3),
        aspect_ratio=aspect_ratio or project.default_aspect_ratio,
        target_platform=target_platform or project.default_platform,
        settings=settings,
        current_revision=1,
    )
    db.add(clip)
    db.flush()
    db.add(
        CaptionTrack(
            organization_id=project.organization_id,
            clip_id=clip.id,
            enabled=True,
            preset=preset,
            style=_default_style(brand_kit),
            text_overrides={},
            version=1,
        )
    )
    db.add(
        ClipRevision(
            clip_id=clip.id,
            revision=1,
            title=clip.title,
            start_seconds=clip.start_seconds,
            end_seconds=clip.end_seconds,
            settings=clip.settings,
            note="Created",
            created_by_id=created_by_id,
        )
    )
    if candidate is not None:
        candidate.status = CandidateStatus.APPROVED
    db.flush()
    return clip


def record_revision(
    db: Session,
    clip: Clip,
    *,
    created_by_id: uuid.UUID | None,
    note: str | None = None,
) -> ClipRevision:
    clip.current_revision += 1
    revision = ClipRevision(
        clip_id=clip.id,
        revision=clip.current_revision,
        title=clip.title,
        start_seconds=clip.start_seconds,
        end_seconds=clip.end_seconds,
        settings=clip.settings,
        note=note,
        created_by_id=created_by_id,
    )
    db.add(revision)
    return revision


def caption_track_for(db: Session, clip: Clip) -> CaptionTrack:
    track = db.execute(
        select(CaptionTrack).where(CaptionTrack.clip_id == clip.id)
    ).scalar_one_or_none()
    if track is None:
        track = CaptionTrack(
            organization_id=clip.organization_id,
            clip_id=clip.id,
            enabled=True,
            preset=VisualPreset.BOLD_CREATOR,
            style=style_for_preset(VisualPreset.BOLD_CREATOR),
            text_overrides={},
        )
        db.add(track)
        db.flush()
    return track


def clip_duration(clip: Clip) -> float:
    return round(max(0.0, clip.end_seconds - clip.start_seconds), 3)
