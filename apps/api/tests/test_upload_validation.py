"""Upload and URL-import validation: never trust the client."""

from __future__ import annotations

import io

import pytest

from app.config import settings
from app.services.file_validation import (
    UnsupportedMedia,
    sanitize_filename,
    validate_upload,
)
from app.services.importer import ImportRejected, validate_import_url

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


def _upload(client, auth, project, payload: bytes, filename: str, mime: str = "video/mp4"):
    return client.post(
        f"/api/v1/media/projects/{project['id']}/upload",
        files={"file": (filename, io.BytesIO(payload), mime)},
        headers=auth,
    )


def test_unsupported_extension_is_rejected(client, auth, project):
    response = _upload(client, auth, project, b"MZ\x90\x00fake exe", "payload.exe", "application/octet-stream")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_extension"


def test_extension_signature_mismatch_is_rejected(client, auth, project):
    response = _upload(client, auth, project, b"just plain text, not a video", "clip.mp4")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_signature"


def test_mime_extension_mismatch_is_rejected(client, auth, project):
    response = _upload(client, auth, project, MP4_HEAD, "clip.mp4", "audio/mpeg")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "mime_extension_mismatch"


def test_empty_upload_is_rejected(client, auth, project):
    response = _upload(client, auth, project, b"", "clip.mp4")
    assert response.status_code in {400, 415, 422}
    assert response.json()["error"]["code"] in {
        "empty_file",
        "invalid_signature",
        "validation_error",
    }


def test_oversized_metadata_is_rejected_before_storage():
    with pytest.raises(UnsupportedMedia) as excinfo:
        validate_upload(
            filename="huge.mp4",
            declared_mime="video/mp4",
            head=MP4_HEAD,
            size_bytes=(settings.max_upload_mb + 1) * 1024 * 1024,
            checksum="0" * 64,
        )
    assert excinfo.value.code == "file_too_large"


def test_filenames_are_sanitized():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert "/" not in sanitize_filename("C:\\Users\\me\\clip.mp4")
    assert sanitize_filename("") == "upload"


@pytest.mark.parametrize(
    "url,reason",
    [
        ("file:///etc/passwd", "scheme"),
        ("ftp://example.com/clip.mp4", "scheme"),
        ("http://localhost/clip.mp4", "local"),
        ("http://127.0.0.1:8000/clip.mp4", "private"),
        ("http://169.254.169.254/latest/meta-data", "private"),
        ("http://10.0.0.5/internal.mp4", "private"),
        ("http://user:pass@example.com/clip.mp4", "credentials"),
        ("http://metadata.google.internal/computeMetadata/v1", "metadata"),
    ],
)
def test_import_url_rejections(url, reason):
    with pytest.raises(ImportRejected) as excinfo:
        validate_import_url(url)
    assert excinfo.value, reason


def test_import_endpoint_rejects_private_host(client, auth, project):
    response = client.post(
        f"/api/v1/media/projects/{project['id']}/import",
        json={"url": "http://127.0.0.1:22/clip.mp4"},
        headers=auth,
    )
    assert response.status_code in {400, 422}
    body = response.json()["error"]
    message = body["message"].lower()
    assert "private" in message or "reserved" in message or "url" in message
    assert body["code"] in {"import_rejected", "import_url_rejected", "validation_error"}
