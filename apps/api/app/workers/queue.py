"""Durable PostgreSQL-backed job queue.

Redis/Celery are deliberately not required: job state lives in the same
transactional database as the rest of the product, so progress survives API and
worker restarts, and no extra broker has to be operated. The semantics mirror a
Celery deployment (leases, heartbeats, retries with exponential backoff,
cancellation) and the interface is small enough to swap for Celery later.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ACTIVE_JOB_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobStatus,
    JobType,
    ProcessingJob,
)

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enqueue(
    db: Session,
    *,
    organization_id: uuid.UUID,
    job_type: JobType,
    payload: dict[str, Any] | None = None,
    project_id: uuid.UUID | None = None,
    media_asset_id: uuid.UUID | None = None,
    clip_id: uuid.UUID | None = None,
    export_id: uuid.UUID | None = None,
    social_post_id: uuid.UUID | None = None,
    created_by_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    priority: int = 0,
    max_attempts: int | None = None,
) -> ProcessingJob:
    """Create a QUEUED job. The caller owns the transaction/commit."""
    job = ProcessingJob(
        organization_id=organization_id,
        type=job_type,
        status=JobStatus.QUEUED,
        progress=0,
        stage_message="Queued",
        payload=payload or {},
        project_id=project_id,
        media_asset_id=media_asset_id,
        clip_id=clip_id,
        export_id=export_id,
        social_post_id=social_post_id,
        created_by_id=created_by_id,
        idempotency_key=idempotency_key,
        priority=priority,
        max_attempts=max_attempts or settings.job_max_attempts,
        next_attempt_at=utcnow(),
    )
    if idempotency_key:
        existing = db.execute(
            select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    db.add(job)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise
    return job


def claim_next(db: Session, worker_id: str) -> ProcessingJob | None:
    """Atomically lease the next runnable job for ``worker_id``."""
    now = utcnow()
    runnable = and_(
        or_(
            ProcessingJob.status == JobStatus.QUEUED,
            and_(
                ProcessingJob.status.in_([status for status in ACTIVE_JOB_STATUSES if status != JobStatus.QUEUED]),
                or_(
                    ProcessingJob.lease_expires_at.is_(None),
                    ProcessingJob.lease_expires_at < now,
                ),
            ),
        ),
        or_(ProcessingJob.next_attempt_at.is_(None), ProcessingJob.next_attempt_at <= now),
        ProcessingJob.cancellation_requested.is_(False),
    )
    stmt = (
        select(ProcessingJob)
        .where(runnable)
        .order_by(ProcessingJob.priority.desc(), ProcessingJob.created_at.asc())
        .limit(1)
    )
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update(skip_locked=True)
    job = db.execute(stmt).scalars().first()
    if job is None:
        return None
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=settings.job_lease_seconds)
    job.heartbeat_at = now
    job.attempts += 1
    job.started_at = job.started_at or now
    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.VALIDATING
    job.stage_message = job.stage_message or "Starting"
    db.commit()
    return job


def heartbeat(db: Session, job: ProcessingJob) -> bool:
    """Extend the lease. Returns False when the job was cancelled meanwhile."""
    db.refresh(job)
    now = utcnow()
    result = db.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job.id)
        .values(
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
        )
    )
    db.commit()
    _ = result
    return not job.cancellation_requested


def request_cancel(db: Session, job: ProcessingJob) -> ProcessingJob:
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    job.cancellation_requested = True
    if job.status == JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = utcnow()
        job.stage_message = "Cancelled before start"
    else:
        job.stage_message = "Cancellation requested"
    db.commit()
    return job


def schedule_retry(db: Session, job: ProcessingJob, *, error_code: str, error_message: str) -> bool:
    """Return True when the job was re-queued, False when attempts are exhausted."""
    if job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED
        job.error_code = error_code
        job.error_message = error_message[:4000]
        job.finished_at = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
        return False
    delay = settings.job_backoff_seconds * (2 ** max(0, job.attempts - 1))
    job.status = JobStatus.QUEUED
    job.error_code = error_code
    job.error_message = error_message[:4000]
    job.next_attempt_at = utcnow() + timedelta(seconds=delay)
    job.lease_owner = None
    job.lease_expires_at = None
    job.stage_message = f"Retry in {delay}s (attempt {job.attempts}/{job.max_attempts})"
    db.commit()
    return True


def mark_completed(db: Session, job: ProcessingJob, result: dict[str, Any]) -> None:
    job.status = JobStatus.COMPLETED
    job.progress = 100
    job.stage_message = "Completed"
    job.result = result
    job.finished_at = utcnow()
    job.lease_owner = None
    job.lease_expires_at = None
    db.commit()


def mark_cancelled(db: Session, job: ProcessingJob) -> None:
    job.status = JobStatus.CANCELLED
    job.stage_message = "Cancelled"
    job.finished_at = utcnow()
    job.lease_owner = None
    job.lease_expires_at = None
    db.commit()


def recover_expired_jobs(db: Session) -> int:
    """Re-queue jobs whose worker died (lease expired and not heartbeating)."""
    now = utcnow()
    stale = (
        db.execute(
            select(ProcessingJob).where(
                ProcessingJob.lease_expires_at.is_not(None),
                ProcessingJob.lease_expires_at < now,
                ProcessingJob.status.in_([s for s in ACTIVE_JOB_STATUSES if s != JobStatus.QUEUED]),
            )
        )
        .scalars()
        .all()
    )
    recovered = 0
    for job in stale:
        job.lease_owner = None
        job.lease_expires_at = None
        job.next_attempt_at = now
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.FAILED
            job.error_code = "worker_lost"
            job.error_message = "Worker stopped responding and retries were exhausted"
            job.finished_at = now
        else:
            job.status = JobStatus.QUEUED
            job.stage_message = "Recovered after worker restart"
        recovered += 1
    if recovered:
        db.commit()
    return recovered


def enqueue_due_scheduled_posts(db: Session) -> int:
    """Turn due ScheduledPost rows into publish jobs (idempotent)."""
    from app.models import ScheduledPost, ScheduledPostStatus, SocialPost, SocialPostStatus

    now = utcnow()
    due = (
        db.execute(
            select(ScheduledPost).where(
                ScheduledPost.status == ScheduledPostStatus.PENDING,
                ScheduledPost.scheduled_for <= now,
            )
        )
        .scalars()
        .all()
    )
    count = 0
    for scheduled in due:
        post = db.get(SocialPost, scheduled.social_post_id)
        if post is None or post.status in {SocialPostStatus.PUBLISHED, SocialPostStatus.CANCELLED}:
            scheduled.status = ScheduledPostStatus.CANCELLED
            continue
        existing = db.execute(
            select(ProcessingJob).where(
                ProcessingJob.social_post_id == post.id,
                ProcessingJob.type == JobType.SOCIAL_PUBLISH,
                ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        job = enqueue(
            db,
            organization_id=post.organization_id,
            job_type=JobType.SOCIAL_PUBLISH,
            payload={"social_post_id": str(post.id), "scheduled": True},
            social_post_id=post.id,
            project_id=post.project_id,
            idempotency_key=f"scheduled-publish:{scheduled.id}",
        )
        scheduled.status = ScheduledPostStatus.ENQUEUED
        scheduled.job_id = job.id
        post.status = SocialPostStatus.PUBLISHING
        count += 1
    if count:
        db.commit()
    return count
