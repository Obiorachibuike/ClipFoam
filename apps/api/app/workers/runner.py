"""Worker runtime: claims jobs, renews leases, dispatches handlers.

Run with `python -m app.workers.worker`. The same code powers the inline worker
used by single-process development, so behaviour is identical in both modes.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, engine
from app.errors import AppError
from app.models import JobStatus, JobType, ProcessingJob
from app.workers.handlers import JobCancelled, JobContext, PermanentJobError, dispatch
from app.workers.queue import (
    claim_next,
    enqueue_due_scheduled_posts,
    heartbeat,
    mark_cancelled,
    mark_completed,
    recover_expired_jobs,
    schedule_retry,
    utcnow,
)

logger = logging.getLogger(__name__)

_shutdown = threading.Event()


class HeartbeatThread(threading.Thread):
    """Keeps the lease alive and notices cancellation requests."""

    def __init__(self, job_id: uuid.UUID, interval: float = 15.0) -> None:
        super().__init__(daemon=True, name=f"heartbeat-{job_id}")
        self.job_id = job_id
        self.interval = interval
        self.cancelled = threading.Event()
        self.stop_flag = threading.Event()

    def run(self) -> None:
        while not self.stop_flag.wait(self.interval):
            db = SessionLocal()
            try:
                job = db.get(ProcessingJob, self.job_id)
                if job is None:
                    break
                alive = heartbeat(db, job)
                if not alive:
                    self.cancelled.set()
                    logger.info("Job %s cancellation observed by heartbeat", self.job_id)
                    break
            except Exception as exc:  # noqa: BLE001 - never kill the heartbeat loop
                logger.warning("Heartbeat failed for job %s: %s", self.job_id, exc)
            finally:
                db.close()

    def stop(self) -> None:
        self.stop_flag.set()


def execute_job(job_id: uuid.UUID, worker_id: str) -> None:
    db = SessionLocal()
    beat = HeartbeatThread(job_id)
    job = db.get(ProcessingJob, job_id)
    if job is None:
        db.close()
        return
    ctx = JobContext(db=db, job=job, worker_id=worker_id)
    beat.start()
    try:
        logger.info("Job %s (%s) started by %s", job.id, job.type.value, worker_id)
        result = dispatch(ctx)
        db.refresh(job)
        if job.cancellation_requested:
            mark_cancelled(db, job)
            logger.info("Job %s cancelled", job.id)
        else:
            mark_completed(db, job, {**(job.result or {}), **result})
            logger.info("Job %s completed", job.id)
    except JobCancelled:
        db.rollback()
        db.refresh(job)
        mark_cancelled(db, job)
        logger.info("Job %s cancelled by user", job.id)
    except PermanentJobError as exc:
        db.rollback()
        db.refresh(job)
        job.status = JobStatus.FAILED
        job.error_code = exc.code
        job.error_message = exc.message[:4000]
        job.finished_at = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
        logger.warning("Job %s failed permanently: %s", job.id, exc.message)
    except AppError as exc:
        db.rollback()
        db.refresh(job)
        requeued = schedule_retry(db, job, error_code=exc.code, error_message=exc.message)
        logger.warning("Job %s error (%s), requeued=%s", job.id, exc.code, requeued)
    except Exception as exc:  # noqa: BLE001 - unknown failures must not kill the worker
        logger.exception("Job %s crashed", job.id)
        db.rollback()
        db.refresh(job)
        if beat.cancelled.is_set() or job.cancellation_requested:
            mark_cancelled(db, job)
        else:
            schedule_retry(db, job, error_code="unhandled_error", error_message=str(exc))
    finally:
        beat.stop()
        db.close()


def run_worker(
    *, concurrency: int | None = None, identity: str | None = None, run_forever: bool = True
) -> None:
    concurrency = concurrency or settings.worker_concurrency
    worker_id = f"{identity or socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    logger.warning("ClipFoam worker %s starting (concurrency=%d)", worker_id, concurrency)

    try:
        engine.connect().close()
    except Exception as exc:  # noqa: BLE001 - surface configuration problems early
        logger.error("Database unavailable: %s", exc)
        raise

    pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job")
    last_housekeeping = 0.0
    active: set[str] = set()
    try:
        while not _shutdown.is_set():
            now = time.monotonic()
            if now - last_housekeeping > 20:
                _housekeeping()
                last_housekeeping = now
            if len(active) >= concurrency:
                time.sleep(0.2)
                active = {key for key in active if not key.endswith(":done")}
                continue
            db = SessionLocal()
            try:
                job = claim_next(db, worker_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to claim job: %s", exc)
                job = None
            finally:
                db.close()
            if job is None:
                if not run_forever:
                    break
                time.sleep(settings.worker_poll_interval)
                continue
            active.add(str(job.id))
            future = pool.submit(_run_and_untrack, job.id, worker_id, active)
            _ = future
    except KeyboardInterrupt:  # pragma: no cover - interactive stop
        logger.warning("Worker interrupted")
    finally:
        _shutdown.set()
        pool.shutdown(wait=True, cancel_futures=False)
        logger.warning("ClipFoam worker %s stopped", worker_id)


def _run_and_untrack(job_id: uuid.UUID, worker_id: str, active: set[str]) -> None:
    try:
        execute_job(job_id, worker_id)
    finally:
        active.discard(str(job_id))


def _housekeeping() -> None:
    db = SessionLocal()
    try:
        recovered = recover_expired_jobs(db)
        if recovered:
            logger.warning("Recovered %d job(s) with expired leases", recovered)
        due = enqueue_due_scheduled_posts(db)
        if due:
            logger.info("Enqueued %d scheduled post(s)", due)
        # Periodic retention/temp cleanup, rate-limited through an idempotency key per hour.
        bucket = utcnow().strftime("%Y%m%d%H")
        existing = db.execute(
            select(ProcessingJob).where(ProcessingJob.idempotency_key == f"cleanup:{bucket}")
        ).scalar_one_or_none()
        if existing is None:
            org = db.execute(select(ProcessingJob.organization_id)).first()
            if org is not None:
                from app.workers.queue import enqueue

                enqueue(
                    db,
                    organization_id=org[0],
                    job_type=JobType.MAINTENANCE_CLEANUP,
                    payload={},
                    idempotency_key=f"cleanup:{bucket}",
                    priority=-10,
                )
                db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Housekeeping failed: %s", exc)
        db.rollback()
    finally:
        db.close()


def stop_worker(*_args) -> None:
    _shutdown.set()


def install_signal_handlers() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, stop_worker)
        except ValueError:  # pragma: no cover - non-main thread
            pass


def process_until_idle(max_iterations: int = 200) -> int:
    """Drain the queue synchronously (used by tests and one-shot CLI runs)."""
    processed = 0
    worker_id = f"inline-{os.getpid()}"
    for _ in range(max_iterations):
        db = SessionLocal()
        try:
            job = claim_next(db, worker_id)
        finally:
            db.close()
        if job is None:
            break
        execute_job(job.id, worker_id)
        processed += 1
    return processed
