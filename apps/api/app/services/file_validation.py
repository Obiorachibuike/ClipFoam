"""Upload validation: extension, MIME type and container signature.

A file is only accepted when its extension is allowed, the declared MIME type
is plausible, and the first bytes match a known media container.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.errors import UnsupportedMedia, ValidationError

# extension -> (kind, allowed mime types, signature detector key)
ALLOWED_VIDEO = {
    "mp4": "video/mp4",
    "m4v": "video/x-m4v",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "avi": "video/x-msvideo",
}
ALLOWED_AUDIO = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "opus": "audio/opus",
}

MIME_ALIASES = {
    "video/quicktime": {"mov"},
    "video/mp4": {"mp4", "m4v", "m4a"},
    "video/x-m4v": {"m4v", "mp4"},
    "audio/mp4": {"m4a", "mp4"},
    "audio/mpeg": {"mp3"},
    "audio/x-m4a": {"m4a"},
    "audio/wav": {"wav"},
    "audio/x-wav": {"wav"},
    "video/x-matroska": {"mkv"},
    "video/webm": {"webm"},
    "audio/webm": {"webm"},
    "application/octet-stream": set(ALLOWED_VIDEO) | set(ALLOWED_AUDIO),
}

FILENAME_RE = re.compile(r"[^A-Za-z0-9._\- ]+")


@dataclass
class ValidatedFile:
    filename: str
    extension: str
    mime_type: str
    kind: str
    size_bytes: int
    checksum_sha256: str


def sanitize_filename(name: str) -> str:
    cleaned = FILENAME_RE.sub("_", Path(name).name).strip(" ._")
    return cleaned[:200] or "upload"


def _detect_container(head: bytes) -> str | None:
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in {b"qt  "}:
            return "mov"
        if brand.startswith(b"M4A"):
            return "m4a"
        if brand.startswith(b"M4V"):
            return "m4v"
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        # Matroska or WebM; the doc type string follows the EBML header.
        if b"webm" in head[:64]:
            return "webm"
        return "mkv"
    if head.startswith(b"RIFF") and len(head) >= 12:
        if head[8:12] == b"AVI ":
            return "avi"
        if head[8:12] == b"WAVE":
            return "wav"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or head[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "mp3"
    if head.startswith(b"ADIF") or head.startswith(b"\xff\xf1"):
        return "aac"
    return None


def validate_upload(
    *,
    filename: str,
    declared_mime: str | None,
    head: bytes,
    size_bytes: int,
    checksum: str,
) -> ValidatedFile:
    safe_name = sanitize_filename(filename)
    extension = Path(safe_name).suffix.lower().lstrip(".")
    if not extension:
        raise UnsupportedMedia("File has no extension", code="missing_extension")
    if extension not in settings.video_extensions and extension not in settings.audio_extensions:
        raise UnsupportedMedia(
            f"'.{extension}' files are not supported. Allowed video: "
            f"{', '.join(sorted(settings.video_extensions))}. Allowed audio: "
            f"{', '.join(sorted(settings.audio_extensions))}.",
            code="unsupported_extension",
        )
    if size_bytes <= 0:
        raise ValidationError("Uploaded file is empty", code="empty_file")
    if size_bytes > settings.max_upload_mb * 1024 * 1024:
        raise UnsupportedMedia(
            f"File exceeds the {settings.max_upload_mb} MB limit", code="file_too_large"
        )

    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared and declared not in {"application/octet-stream", "", "binary/octet-stream"}:
        allowed_ext = MIME_ALIASES.get(declared)
        if allowed_ext is not None and extension not in allowed_ext:
            raise UnsupportedMedia(
                f"Declared type '{declared}' does not match the '.{extension}' extension",
                code="mime_extension_mismatch",
            )

    container = _detect_container(head)
    if container is None:
        raise UnsupportedMedia(
            "File signature does not match a supported video/audio container",
            code="invalid_signature",
        )
    # Container family must be compatible with the claimed extension.
    families = {
        "mp4": {"mp4", "m4v", "m4a", "mov"},
        "mov": {"mov", "mp4", "m4v"},
        "m4v": {"m4v", "mp4"},
        "m4a": {"m4a", "mp4"},
        "mkv": {"mkv"},
        "webm": {"webm"},
        "avi": {"avi"},
        "wav": {"wav"},
        "ogg": {"ogg", "opus"},
        "flac": {"flac"},
        "mp3": {"mp3"},
        "aac": {"aac"},
    }
    if extension not in families.get(container, set()):
        raise UnsupportedMedia(
            f"File contents look like '{container}' but the extension is '.{extension}'",
            code="signature_extension_mismatch",
        )

    kind = "video" if extension in settings.video_extensions else "audio"
    resolved_mime = ALLOWED_VIDEO.get(extension) or ALLOWED_AUDIO.get(extension) or "application/octet-stream"
    return ValidatedFile(
        filename=safe_name,
        extension=extension,
        mime_type=resolved_mime,
        kind=kind,
        size_bytes=size_bytes,
        checksum_sha256=checksum,
    )


def hash_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            data = handle.read(chunk)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def read_head(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)
