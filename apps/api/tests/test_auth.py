"""Authentication, session and CSRF behaviour."""

from __future__ import annotations

import uuid

from tests.conftest import run_jobs


def _register(client, email: str | None = None, password: str = "SuperSecret123"):
    email = email or f"user-{uuid.uuid4().hex[:10]}@example.com"
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "name": "Auth Test"},
    )


def test_register_creates_session_cookies_and_organization(client):
    response = _register(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"].startswith("user-")
    assert body["organization"]["slug"]
    assert body["role"] == "owner"
    assert body["csrf_token"]
    set_cookie = " ".join(response.headers.get_list("set-cookie")).lower()
    assert "httponly" in set_cookie
    # The password is never echoed back.
    assert "password" not in {key for key in body["user"]}


def test_duplicate_email_is_rejected(client):
    email = f"dupe-{uuid.uuid4().hex[:8]}@example.com"
    first = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "SuperSecret123", "name": "A"}
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "SuperSecret123", "name": "B"}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "email_taken"


def test_short_password_is_rejected(client):
    response = _register(client, password="short")
    assert response.status_code == 422


def test_login_with_wrong_password_fails(client, account):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_login_does_not_leak_whether_email_exists(client):
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "SuperSecret123"}
    )
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "invalid_credentials"


def test_session_requires_authentication(client):
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401


def test_session_and_logout_flow(client, account):
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["user"]["email"] == account["email"]

    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": account["csrf"]})
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/session").status_code == 401


def test_csrf_header_is_required_for_cookie_writes(client, account, monkeypatch):
    """Cookie-authenticated writes must echo the CSRF cookie in the header.

    CSRF enforcement is intentionally skipped in the ``test`` environment so the
    bearer-token fixtures stay simple, so this test flips the app into
    development mode for the duration of the request.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "app_env", "development")
    response = client.post("/api/v1/projects", json={"title": "No CSRF"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"

    # The same request succeeds once the double-submit token is supplied.
    ok = client.post(
        "/api/v1/projects",
        json={"title": "With CSRF"},
        headers={"X-CSRF-Token": account["csrf"]},
    )
    assert ok.status_code == 201

    # Bearer clients are exempt (no ambient browser credentials).
    bearer = client.post(
        "/api/v1/projects",
        json={"title": "Bearer"},
        headers={"Authorization": "Bearer " + _token_for(account["email"])},
    )
    assert bearer.status_code == 201


def _token_for(email: str) -> str:
    from app.db import SessionLocal
    from app.models import User
    from app.security import create_access_token

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        token, _ = create_access_token(
            user_id=user.id,
            password_hash=user.password_hash,
            org_id=user.memberships[0].organization_id,
        )
        return token
    finally:
        db.close()


def test_password_change_invalidates_old_password(client, account):
    change = client.post(
        "/api/v1/auth/password",
        json={"current_password": account["password"], "new_password": "EvenBetterSecret456"},
        headers={"X-CSRF-Token": account["csrf"]},
    )
    assert change.status_code == 204, change.text

    # Existing cookie session keeps working (token re-issued).
    assert client.get("/api/v1/auth/session").status_code == 200

    client.post("/api/v1/auth/logout")
    old = client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": account["password"]},
    )
    assert old.status_code == 401
    new = client.post(
        "/api/v1/auth/login",
        json={"email": account["email"], "password": "EvenBetterSecret456"},
    )
    assert new.status_code == 200


def test_jobs_are_scoped_to_the_caller(client, auth, project):
    """A freshly created project has no jobs; the list endpoint returns a page."""
    response = client.get("/api/v1/jobs?limit=10", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert "items" in body and "total" in body


def test_transcription_without_provider_fails_honestly(client, auth, project, uploaded_media):
    """The manual provider must refuse rather than fabricate a transcript."""
    response = client.post(
        f"/api/v1/media/{uploaded_media['id']}/transcribe",
        json={"language": None, "force": False},
        headers=auth,
    )
    assert response.status_code == 202, response.text
    run_jobs()
    transcript = client.get(
        f"/api/v1/media/{uploaded_media['id']}/transcript", headers=auth
    ).json()
    assert transcript["status"] == "failed"
    assert transcript["error_message"]
