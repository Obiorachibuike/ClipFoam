"""Durable job states, cancellation, retries and publishing failure paths."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.db import SessionLocal
from app.errors import ProviderUnavailable
from app.models import (
    JobStatus,
    MediaStatus,
    ProcessingJob,
    SocialPost,
    SocialPostStatus,
)
from app.workers import handlers as handlers_module
from app.workers.handlers import JobContext, PermanentJobError, dispatch
from app.workers.queue import claim_next, enqueue
from app.workers.runner import execute_job
from tests.conftest import run_jobs

TERMINAL = {"completed", "failed", "cancelled"}


def test_queued_job_can_be_cancelled_before_it_runs(client, auth, project, transcribed_media):
    response = client.post(
        f"/api/v1/projects/{project['id']}/candidates/generate",
        json={"clip_count": 2, "min_duration": 8, "max_duration": 20, "use_ai": False},
        headers=auth,
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth)
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled"
    assert body["cancellation_requested"] is True

    # Nothing runs afterwards because the job is terminal.
    run_jobs()
    after = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert after["status"] == "cancelled"
    candidates = client.get(f"/api/v1/projects/{project['id']}/candidates", headers=auth).json()
    assert candidates == []


def test_cancelling_a_finished_job_is_rejected(client, auth, project, transcribed_media):
    response = client.post(
        f"/api/v1/projects/{project['id']}/candidates/generate", headers=auth, json={}
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]
    run_jobs()
    second = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "job_finished"


def test_job_states_are_persisted_with_stage_messages(client, auth, project, sample_video):
    with sample_video.open("rb") as handle:
        uploaded = client.post(
            f"/api/v1/media/projects/{project['id']}/upload",
            files={"file": ("sample.mp4", handle, "video/mp4")},
            headers=auth,
        )
    assert uploaded.status_code == 202
    job_id = uploaded.json()["job"]["id"]
    pending = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert pending["status"] in {"queued", "validating", "ingesting"}
    assert pending["type"] == "media.ingest"

    run_jobs()
    done = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert done["status"] == "completed"
    assert done["progress"] == 100
    assert done["started_at"] and done["finished_at"]
    assert done["result"]["media_asset_id"]


def test_retryable_errors_requeue_then_fail_after_max_attempts(monkeypatch, account):
    """A transient provider error must be retried with backoff, then surface as FAILED."""
    from app.models import JobType, OrganizationMembership

    db = SessionLocal()
    try:
        membership = (
            db.execute(OrganizationMembership.__table__.select().limit(1)).first()
        )
        assert membership is not None
        org_id = membership.organization_id

        def boom(ctx: JobContext):
            raise ProviderUnavailable("temporary outage", code="provider_outage")

        monkeypatch.setitem(handlers_module.HANDLERS, JobType.MAINTENANCE_CLEANUP, boom)
        job = enqueue(
            db,
            organization_id=org_id,
            job_type=JobType.MAINTENANCE_CLEANUP,
            payload={},
            max_attempts=2,
        )
        db.commit()
        job_id = job.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        claimed = claim_next(db, "test-worker")
        assert claimed is not None and claimed.id == job_id
    finally:
        db.close()

    execute_job(job_id, "test-worker")
    db = SessionLocal()
    try:
        first = db.get(ProcessingJob, job_id)
        assert first.status == JobStatus.QUEUED
        assert first.attempts == 1
        assert first.error_code == "provider_outage"
        assert first.next_attempt_at is not None
        first.next_attempt_at = None
        db.commit()
        assert claim_next(db, "test-worker") is not None
    finally:
        db.close()

    execute_job(job_id, "test-worker")
    db = SessionLocal()
    try:
        final = db.get(ProcessingJob, job_id)
        assert final.status == JobStatus.FAILED
        assert final.attempts == 2
        assert final.error_message
    finally:
        db.close()


def test_permanent_error_is_not_retried(client, auth, project, uploaded_media, monkeypatch):
    from app.workers.handlers import HANDLERS

    monkeypatch.setitem(
        HANDLERS,
        "media.transcribe",
        lambda ctx: (_ for _ in ()).throw(PermanentJobError("no provider", code="no_provider")),
    )
    response = client.post(
        f"/api/v1/media/{uploaded_media['id']}/transcribe",
        json={"language": None, "force": True},
        headers=auth,
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    run_jobs()
    job = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert job["status"] == "failed"
    assert job["attempts"] == 1
    assert job["error_code"] == "no_provider"


def test_too_short_clip_is_rejected_before_rendering(client, auth, project, transcribed_media):
    clip = client.post(
        f"/api/v1/projects/{project['id']}/clips",
        json={"start_seconds": 1.0, "end_seconds": 1.4, "title": "Too short"},
        headers=auth,
    )
    assert clip.status_code == 422
    assert clip.json()["error"]["code"] == "clip_too_short"


def test_publishing_can_be_disabled_by_configuration(client, auth, project, monkeypatch):
    post = client.post(
        "/api/v1/social/posts",
        json={"platform": "youtube", "body": "hello", "project_id": project["id"]},
        headers=auth,
    )
    assert post.status_code == 201, post.text
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    response = client.post(f"/api/v1/social/posts/{post.json()['id']}/publish", json={}, headers=auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "publishing_disabled"


def test_youtube_publish_requires_a_ready_export(client, auth, project):
    post = client.post(
        "/api/v1/social/posts",
        json={"platform": "youtube", "body": "no export", "project_id": project["id"]},
        headers=auth,
    )
    assert post.status_code == 201
    response = client.post(f"/api/v1/social/posts/{post.json()['id']}/publish", json={}, headers=auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "missing_export"


def test_already_published_posts_cannot_be_published_twice(client, auth, project):
    post = client.post(
        "/api/v1/social/posts",
        json={"platform": "mastodon", "body": "once only", "project_id": project["id"]},
        headers=auth,
    )
    assert post.status_code == 201
    post_id = post.json()["id"]

    db = SessionLocal()
    try:
        row = db.get(SocialPost, uuid.UUID(post_id))
        row.status = SocialPostStatus.PUBLISHED
        row.published_external_id = "external-1"
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/social/posts/{post_id}/publish", json={}, headers=auth)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "already_published"


def test_publish_without_connected_account_fails_and_records_attempt(client, auth, project):
    post = client.post(
        "/api/v1/social/posts",
        json={"platform": "mastodon", "body": "no account", "project_id": project["id"]},
        headers=auth,
    )
    assert post.status_code == 201
    post_id = post.json()["id"]
    queued = client.post(f"/api/v1/social/posts/{post_id}/publish", json={}, headers=auth)
    assert queued.status_code == 202, queued.text

    run_jobs()
    db = SessionLocal()
    try:
        row = db.get(SocialPost, uuid.UUID(post_id))
        assert row.status == SocialPostStatus.FAILED
        assert "connect" in (row.error_message or "").lower()
    finally:
        db.close()

    attempts = client.get(f"/api/v1/social/posts/{post_id}/attempts", headers=auth).json()
    assert attempts, "a failed attempt must be recorded"
    assert attempts[0]["status"] in {"failed", "retrying"}


def test_scheduling_in_the_past_is_rejected(client, auth, project):
    from datetime import datetime, timedelta, timezone

    post = client.post(
        "/api/v1/social/posts",
        json={"platform": "mastodon", "body": "past", "project_id": project["id"]},
        headers=auth,
    )
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    response = client.post(
        f"/api/v1/social/posts/{post.json()['id']}/schedule",
        params={"scheduled_for": past},
        headers=auth,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "schedule_in_past"


def test_duplicate_publish_job_is_rejected_by_idempotency(client, auth, project):
    post = client.post(
        "/api/v1/social/posts",
        json={"platform": "mastodon", "body": "idem", "project_id": project["id"]},
        headers=auth,
    )
    post_id = post.json()["id"]
    first = client.post(f"/api/v1/social/posts/{post_id}/publish", json={}, headers=auth)
    assert first.status_code == 202
    second = client.post(f"/api/v1/social/posts/{post_id}/publish", json={}, headers=auth)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "already_publishing"


def test_unknown_job_type_fails_permanently(db):
    """An unregistered job type must fail loudly instead of being marked complete."""

    class FakeJob:
        type = "not.a.real.job"
        payload: dict = {}

    class FakeCtx:
        job = FakeJob()
        db = None

    with pytest.raises(PermanentJobError):
        dispatch(FakeCtx())  # type: ignore[arg-type]


def test_upload_marks_media_ready_with_real_dimensions(client, auth, project, uploaded_media):
    assert uploaded_media["status"] == MediaStatus.READY.value
    assert uploaded_media["width"] == 640
