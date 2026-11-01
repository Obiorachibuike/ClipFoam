"""Test harness: isolated SQLite database, temp storage, real FFmpeg fixtures."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

TEST_ROOT = Path(__file__).resolve().parent.parent / "data" / "test-artifacts"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

# Configuration must be set before importing app modules (settings are cached).
os.environ.setdefault("CF_APP_ENV", "test")
os.environ.setdefault("CF_DATABASE_URL", f"sqlite:///{TEST_ROOT / 'test.db'}")
os.environ.setdefault("CF_LOCAL_STORAGE_PATH", str(TEST_ROOT / "media"))
os.environ.setdefault("CF_TEMP_WORK_PATH", str(TEST_ROOT / "work"))
os.environ.setdefault("CF_SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")
os.environ.setdefault("CF_TRANSCRIPTION_PROVIDER", "manual")
os.environ.setdefault("CF_AI_PROVIDER", "none")
os.environ.setdefault("CF_STORAGE_BACKEND", "local")
os.environ.setdefault("CF_MAX_UPLOAD_MB", "64")
os.environ.setdefault("CF_RENDER_TIMEOUT_SECONDS", "240")
os.environ.setdefault("CF_SOCIAL_PUBLISH_ENABLED", "true")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine, SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.config import settings  # noqa: E402
from app.storage import reset_storage  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    from app import models  # noqa: F401  ensure metadata is populated

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    reset_storage()
    settings.local_storage_path.mkdir(parents=True, exist_ok=True)
    settings.temp_work_path.mkdir(parents=True, exist_ok=True)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _generate_sample_video(path: Path, duration: int = 24) -> Path:
    """Create a small, self-generated test clip (no third-party media licence issues)."""
    if path.exists():
        return path
    ffmpeg = settings.ffmpeg_path()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x360:rate=30:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, timeout=180, capture_output=True)
    return path


SAMPLE_VIDEO = TEST_ROOT / "sample.mp4"
SAMPLE_VIDEO_ALT = TEST_ROOT / "sample-alt.mp4"


@pytest.fixture(scope="session")
def sample_video() -> Path:
    return _generate_sample_video(SAMPLE_VIDEO, duration=24)


@pytest.fixture(scope="session")
def sample_video_alt() -> Path:
    return _generate_sample_video(SAMPLE_VIDEO_ALT, duration=18)


@pytest.fixture()
def account(client):
    email = f"creator-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SuperSecret123", "name": "Test Creator"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    csrf = client.cookies.get(settings.cookie_csrf_name) or data["csrf_token"]
    return {
        "email": email,
        "password": "SuperSecret123",
        "user": data["user"],
        "organization": data["organization"],
        "csrf": csrf,
    }


@pytest.fixture()
def auth(account):
    return {
        "X-CSRF-Token": account["csrf"],
        "Authorization": "Bearer " + _bearer(account),
    }


def _bearer(account) -> str:
    """Issue a real JWT for the test user (bearer path skips CSRF by design)."""
    from app.models import User
    from app.security import create_access_token

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == account["email"]).one()
        token, _ = create_access_token(
            user_id=user.id, password_hash=user.password_hash, org_id=user.memberships[0].organization_id
        )
        return token
    finally:
        db.close()


@pytest.fixture()
def project(client, auth):
    response = client.post(
        "/api/v1/projects", json={"title": "Test project"}, headers=auth
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def other_account(client):
    email = f"other-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SuperSecret123", "name": "Other Creator"},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    csrf = client.cookies.get(settings.cookie_csrf_name) or data["csrf_token"]
    account = {"email": email, "password": "SuperSecret123", "user": data["user"], "csrf": csrf}
    return {"X-CSRF-Token": csrf, "Authorization": "Bearer " + _bearer(account)}


def run_jobs(limit: int = 50) -> int:
    from app.workers.runner import process_until_idle

    return process_until_idle(limit)


SRT_SAMPLE = """1
00:00:00,500 --> 00:00:04,200
Most people think the biggest mistake in video editing is the software. That is wrong.

2
00:00:04,500 --> 00:00:09,800
The real problem is that they never test the hook in the first three seconds.

3
00:00:10,200 --> 00:00:14,600
Here is the framework I use: pick the promise, cut the setup, and lead with the payoff.

4
00:00:15,000 --> 00:00:19,400
Why does this work? Because the viewer decides to stay or leave in under two seconds.

5
00:00:19,800 --> 00:00:23,500
Test five hooks for every clip and keep the one with the strongest opening line.
"""


@pytest.fixture()
def uploaded_media(client, auth, project, sample_video):
    with sample_video.open("rb") as handle:
        response = client.post(
            f"/api/v1/media/projects/{project['id']}/upload",
            files={"file": ("sample.mp4", handle, "video/mp4")},
            headers=auth,
        )
    assert response.status_code == 202, response.text
    payload = response.json()
    run_jobs()
    media = client.get(f"/api/v1/media/{payload['media']['id']}", headers=auth).json()
    assert media["status"] == "ready", media
    return media


@pytest.fixture()
def transcribed_media(client, auth, uploaded_media):
    response = client.post(
        "/api/v1/transcripts/import",
        params={"media_id": uploaded_media["id"]},
        json={"content": SRT_SAMPLE, "format": "srt"},
        headers=auth,
    )
    assert response.status_code == 201, response.text
    return uploaded_media


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """Keep artifacts small; the test DB/storage is disposable."""
    for child in TEST_ROOT.glob("work/*"):
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
