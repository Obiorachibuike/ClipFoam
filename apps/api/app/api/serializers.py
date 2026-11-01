"""Model → response serialisation helpers (adds signed URLs where needed)."""

from __future__ import annotations

from app.config import settings
from app.models import BrandKit, Export, JobStatus, MediaAsset, ProcessingJob, SocialPost
from app.schemas import (
    BrandKitOut,
    ExportOut,
    JobOut,
    MediaAssetOut,
    SocialPostOut,
)
from app.storage import get_storage


def media_out(media: MediaAsset, *, include_thumb: bool = True) -> MediaAssetOut:
    payload = MediaAssetOut.model_validate(media)
    if include_thumb and media.thumbnail_key:
        try:
            payload.thumbnail_url = get_storage().signed_url(media.thumbnail_key)
        except Exception:  # noqa: BLE001 - a missing thumbnail must not break listing
            payload.thumbnail_url = None
    if include_thumb and media.storage_key and media.status.value == "ready":
        try:
            payload.stream_url = get_storage().signed_url(media.storage_key, ttl_seconds=3600)
        except Exception:  # noqa: BLE001
            payload.stream_url = None
    return payload


def job_out(job: ProcessingJob) -> JobOut:
    return JobOut.model_validate(job)


def export_out(export: Export, *, with_links: bool = False) -> ExportOut:
    payload = ExportOut.model_validate(export)
    if with_links:
        _ = settings.signed_url_ttl_seconds
    return payload


def brand_kit_out(kit: BrandKit) -> BrandKitOut:
    payload = BrandKitOut.model_validate(kit)
    if kit.logo_storage_key:
        try:
            payload.logo_url = get_storage().signed_url(kit.logo_storage_key)
        except Exception:  # noqa: BLE001
            payload.logo_url = None
    return payload


def social_post_out(post: SocialPost) -> SocialPostOut:
    payload = SocialPostOut.model_validate(post)
    return payload


def is_active_job(job: ProcessingJob) -> bool:
    return job.status not in {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
    }
