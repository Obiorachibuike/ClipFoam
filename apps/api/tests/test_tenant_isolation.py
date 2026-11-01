"""Tenant isolation: one workspace must never read or mutate another's data."""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

from app.storage import sign_storage_key
from tests.conftest import run_jobs


def test_other_org_cannot_read_project(client, auth, other_account, project):
    response = client.get(f"/api/v1/projects/{project['id']}", headers=other_account)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project_not_found"

    # ...while the owner still can.
    assert client.get(f"/api/v1/projects/{project['id']}", headers=auth).status_code == 200


def test_other_org_cannot_mutate_or_delete_project(client, auth, other_account, project):
    patched = client.patch(
        f"/api/v1/projects/{project['id']}", json={"title": "Hijacked"}, headers=other_account
    )
    assert patched.status_code == 404

    deleted = client.delete(f"/api/v1/projects/{project['id']}", headers=other_account)
    assert deleted.status_code == 404

    owner_view = client.get(f"/api/v1/projects/{project['id']}", headers=auth).json()
    assert owner_view["title"] == "Test project"


def test_project_listing_is_scoped_per_org(client, auth, other_account, project):
    mine = client.get("/api/v1/projects?limit=100", headers=auth).json()
    theirs = client.get("/api/v1/projects?limit=100", headers=other_account).json()
    assert all(item["id"] != project["id"] for item in theirs["items"])
    assert any(item["id"] == project["id"] for item in mine["items"])


def test_media_and_transcript_are_not_shared(client, auth, other_account, transcribed_media):
    media_id = transcribed_media["id"]
    assert client.get(f"/api/v1/media/{media_id}", headers=other_account).status_code == 404
    assert (
        client.get(f"/api/v1/media/{media_id}/transcript", headers=other_account).status_code
        == 404
    )
    transcript = client.get(f"/api/v1/media/{media_id}/transcript", headers=auth).json()
    assert (
        client.get(f"/api/v1/transcripts/{transcript['id']}", headers=other_account).status_code
        == 404
    )


def test_clip_and_candidate_access_is_scoped(client, auth, other_account, project, transcribed_media):
    client.post(
        f"/api/v1/projects/{project['id']}/candidates/generate",
        json={"clip_count": 2, "min_duration": 10, "max_duration": 25, "use_ai": False},
        headers=auth,
    )
    run_jobs()
    candidates = client.get(f"/api/v1/projects/{project['id']}/candidates", headers=auth).json()
    assert candidates
    candidate_id = candidates[0]["id"]

    assert (
        client.get(f"/api/v1/candidates/{candidate_id}", headers=other_account).status_code == 404
    )
    assert (
        client.post(f"/api/v1/candidates/{candidate_id}/approve", headers=other_account).status_code
        == 404
    )

    clip = client.post(f"/api/v1/candidates/{candidate_id}/approve", headers=auth)
    assert clip.status_code == 201, clip.text
    clip_id = clip.json()["id"]
    assert client.get(f"/api/v1/clips/{clip_id}", headers=other_account).status_code == 404
    assert (
        client.patch(f"/api/v1/clips/{clip_id}", json={"title": "x"}, headers=other_account).status_code
        == 404
    )
    assert client.get(f"/api/v1/clips/{clip_id}/captions", headers=other_account).status_code == 404


def test_signed_file_urls_reject_tampering(client, auth, uploaded_media):
    from urllib.parse import parse_qs

    url = uploaded_media["stream_url"]
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert "signature" in query and "expires" in query

    # Unsigned access is denied.
    unsigned = client.get(parsed.path)
    assert unsigned.status_code in {403, 422}

    # Flipping the signature is denied.
    forged = f"{parsed.path}?expires={query['expires'][0]}&signature={'0' * 64}"
    assert client.get(forged).status_code == 403

    # An expired signature is denied.
    expired = sign_storage_key(_key_of(auth, uploaded_media), ttl_seconds=-5)
    expired_url = f"{parsed.path}?expires={expired['expires']}&signature={expired['signature']}"
    assert client.get(expired_url).status_code == 403


def _key_of(auth, media) -> str:  # noqa: ARG001 - signature helper kept explicit for readability
    """Derive the storage key from the media row (not from the signed URL)."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import MediaAsset

    db = SessionLocal()
    try:
        row = db.execute(
            select(MediaAsset).where(MediaAsset.id == uuid.UUID(media["id"]))
        ).scalar_one()
        return row.storage_key
    finally:
        db.close()


def test_audit_log_requires_admin_and_is_scoped(client, auth, other_account):
    owner_view = client.get("/api/v1/system/audit-logs?limit=20", headers=auth)
    assert owner_view.status_code == 200
    entries = owner_view.json()["items"]
    assert entries, "creating a project should record an audit entry"

    other_view = client.get("/api/v1/system/audit-logs?limit=20", headers=other_account)
    assert other_view.status_code == 200
    other_ids = {entry["id"] for entry in other_view.json()["items"]}
    assert not other_ids & {entry["id"] for entry in entries}
