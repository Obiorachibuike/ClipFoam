"""Signed media delivery.

Local storage is private: objects are only served through short-lived HMAC
signed URLs produced by the API. S3/R2 uses its own presigned URLs instead.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.config import settings
from app.errors import PermissionDenied, NotFoundError
from app.security import verify_storage_signature
from app.storage import LocalStorage, get_storage

router = APIRouter(tags=["files"])

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
    ".ass": "text/x-ssa",
}


@router.get("/files/{key:path}")
def serve_file(
    key: str,
    expires: int | None = Query(default=None),
    signature: str | None = Query(default=None),
) -> FileResponse:
    if expires is None or signature is None or not verify_storage_signature(key, expires, signature):
        raise PermissionDenied("This media link is invalid or has expired", code="invalid_signature")
    storage = get_storage()
    if not isinstance(storage, LocalStorage):
        raise NotFoundError("This deployment serves media through signed object-storage URLs")
    try:
        path = storage.open_local(key)
    except Exception as exc:  # noqa: BLE001 - missing object
        raise NotFoundError("Media object not found", code="object_not_found") from exc
    suffix = Path(key).suffix.lower()
    media_type = CONTENT_TYPES.get(suffix, "application/octet-stream")
    filename = Path(key).name
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=300", "Content-Disposition": f'inline; filename="{Path(filename).name}"'},
    )


_ = settings
