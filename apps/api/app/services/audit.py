"""Audit trails and usage metering."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, UsageKind, UsageRecord


def record_audit(
    db: Session,
    *,
    action: str,
    organization_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata_json=metadata or {},
        ip_address=None,
        user_agent=None,
    )
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        entry.ip_address = (forwarded.split(",")[0].strip() if forwarded else request.client.host if request.client else None)
        entry.user_agent = (request.headers.get("user-agent") or "")[:400]
    db.add(entry)
    return entry


def record_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    kind: UsageKind,
    quantity: float,
    unit: str = "seconds",
    project_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    provider: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> UsageRecord:
    entry = UsageRecord(
        organization_id=organization_id,
        kind=kind,
        quantity=round(float(quantity), 3),
        unit=unit,
        project_id=project_id,
        job_id=job_id,
        user_id=user_id,
        provider=provider,
        metadata_json=metadata or {},
    )
    db.add(entry)
    return entry
