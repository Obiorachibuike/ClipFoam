"""Background job inspection, cancellation and SSE progress streams."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, ValidationError
from app.models import JobStatus, ProcessingJob
from app.schemas import JobEvent, JobOut, Page
from app.services.audit import record_audit
from app.api.serializers import job_out
from app.db import SessionLocal
from app.security import decode_access_token
from app.workers.queue import request_cancel

router = APIRouter(prefix="/jobs", tags=["jobs"])

TERMINAL = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


@router.get("", response_model=Page[JobOut])
def list_jobs(
    db: DbSession,
    ctx: Ctx,
    active_only: bool = False,
    project_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[JobOut]:
    stmt = select(ProcessingJob).where(ProcessingJob.organization_id == ctx.org_id)
    if active_only:
        stmt = stmt.where(ProcessingJob.status.notin_(list(TERMINAL)))
    if project_id:
        stmt = stmt.where(ProcessingJob.project_id == project_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    jobs = (
        db.execute(stmt.order_by(ProcessingJob.created_at.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return Page[JobOut](
        items=[job_out(job) for job in jobs], total=int(total), limit=limit, offset=offset
    )


@router.get("/{job_id}", response_model=JobOut)
def read_job(job_id: uuid.UUID, db: DbSession, ctx: Ctx) -> JobOut:
    job = db.execute(
        select(ProcessingJob).where(
            ProcessingJob.id == job_id, ProcessingJob.organization_id == ctx.org_id
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job not found", code="job_not_found")
    return job_out(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> JobOut:
    verify_csrf(request)
    job = db.execute(
        select(ProcessingJob).where(
            ProcessingJob.id == job_id, ProcessingJob.organization_id == ctx.org_id
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job not found", code="job_not_found")
    if job.status in TERMINAL:
        raise ValidationError("This job has already finished", code="job_finished")
    request_cancel(db, job)
    record_audit(
        db,
        action="job.cancelled",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="job",
        target_id=str(job.id),
        request=request,
    )
    db.commit()
    return job_out(job)


def _event_for(job: ProcessingJob) -> str:
    payload = JobEvent(
        job_id=job.id,
        status=job.status,
        progress=job.progress,
        stage_message=job.stage_message,
        error_code=job.error_code,
        error_message=job.error_message,
        result=job.result or {},
        updated_at=job.updated_at,
    )
    return f"event: job\ndata: {json.dumps(payload.model_dump(mode='json'))}\n\n"


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: uuid.UUID,
    request: Request,
    token: Annotated[str | None, Query()] = None,
    timeout_seconds: Annotated[int, Query(ge=5, le=1800)] = 600,
) -> StreamingResponse:
    """Server-Sent Events stream of job progress (authenticated + authorized)."""
    raw_token = token or request.cookies.get("clipfoam_session") or ""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        raw_token = auth_header[7:].strip()
    if not raw_token:
        raise ValidationError("Authentication required for progress streams", code="unauthorized")
    try:
        claims = decode_access_token(raw_token)
    except Exception as exc:  # noqa: BLE001 - jwt raises several subclasses
        raise ValidationError("Invalid session for progress stream", code="unauthorized") from exc
    user_id = claims.get("sub")

    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if job is None:
            raise NotFoundError("Job not found", code="job_not_found")
        org_id = job.organization_id
        authorized = db.execute(
            select(func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.organization_id == org_id)
        ).scalar_one()
        _ = authorized
        from app.models import OrganizationMembership

        membership = db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == uuid.UUID(str(user_id)),
                OrganizationMembership.organization_id == org_id,
            )
        ).scalar_one_or_none()
        if membership is None:
            raise NotFoundError("Job not found", code="job_not_found")
    finally:
        db.close()

    async def event_generator():
        sent_terminal = False
        elapsed = 0
        last_state: tuple | None = None
        while elapsed < timeout_seconds:
            if await request.is_disconnected():
                break
            session = SessionLocal()
            try:
                current = session.get(ProcessingJob, job_id)
                if current is None:
                    break
                state = (current.status, current.progress, current.stage_message)
                if state != last_state:
                    yield _event_for(current)
                    last_state = state
                else:
                    yield ": keep-alive\n\n"
                if current.status in TERMINAL:
                    sent_terminal = True
                    break
            finally:
                session.close()
            await asyncio.sleep(1.0)
            elapsed += 1
        if not sent_terminal:
            yield f"event: timeout\ndata: {json.dumps({'job_id': str(job_id), 'timeout': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
