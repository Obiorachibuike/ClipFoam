"""Request dependencies: authentication, tenant context, authorization, rate limits."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.errors import PermissionDenied, RateLimitError, Unauthorized
from app.models import MembershipRole, Organization, OrganizationMembership, User
from app.security import csrf_tokens_match, decode_access_token

DbSession = Annotated[Session, Depends(get_db)]

ROLE_RANK: dict[MembershipRole, int] = {
    MembershipRole.VIEWER: 1,
    MembershipRole.EDITOR: 2,
    MembershipRole.ADMIN: 3,
    MembershipRole.OWNER: 4,
}


@dataclass
class AuthContext:
    user: User
    organization: Organization
    role: MembershipRole

    @property
    def org_id(self) -> uuid.UUID:
        return self.organization.id

    def can_write(self) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[MembershipRole.EDITOR]

    def is_admin(self) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[MembershipRole.ADMIN]


# --------------------------------------------------------------------------- rate limiting
class _SlidingWindow:
    """Small in-process sliding-window limiter.

    Adequate for a single API instance; production operators put a reverse
    proxy/Redis limiter in front (documented in docs/operations.md).
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


_limiter = _SlidingWindow()


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit(request: Request) -> None:
    if settings.is_test:
        return
    if not _limiter.check(f"general:{_client_key(request)}", settings.rate_limit_per_minute):
        raise RateLimitError("Too many requests. Slow down and retry shortly.")


def login_rate_limiter(request: Request) -> None:
    if settings.is_test:
        return
    if not _limiter.check(
        f"login:{_client_key(request)}", settings.login_rate_limit_per_minute, window=60.0
    ):
        raise RateLimitError(
            "Too many authentication attempts. Wait a minute and try again.",
            code="login_rate_limited",
        )


# --------------------------------------------------------------------------- auth
def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(settings.cookie_name)


def _user_from_token(db: Session, token: str) -> User:
    try:
        payload = decode_access_token(token)
    except Exception as exc:  # noqa: BLE001 - jwt raises several subclasses
        raise Unauthorized("Session expired or invalid. Sign in again.") from exc
    user_id = payload.get("sub")
    if not user_id:
        raise Unauthorized("Malformed session token.")
    try:
        user_uuid = uuid.UUID(str(user_id))
    except ValueError as exc:
        raise Unauthorized("Malformed session token.") from exc
    user = db.get(User, user_uuid)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise Unauthorized("Account not available.")
    from app.security import password_fingerprint

    if payload.get("pwv") and payload["pwv"] != password_fingerprint(user.password_hash):
        raise Unauthorized("Session no longer valid after a password change.")
    return user


def get_current_user(request: Request, db: DbSession) -> User:
    token = _extract_token(request)
    if not token:
        raise Unauthorized("Authentication required.")
    return _user_from_token(db, token)


CurrentUser = Annotated[User, Depends(get_current_user)]


def _resolve_membership(db: Session, user: User, org_header: str | None) -> OrganizationMembership:
    stmt = (
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            Organization.deleted_at.is_(None),
        )
    )
    if org_header:
        try:
            org_uuid = uuid.UUID(org_header)
        except ValueError as exc:
            raise PermissionDenied("Invalid organization header.") from exc
        stmt = stmt.where(OrganizationMembership.organization_id == org_uuid)
    membership = db.execute(stmt.order_by(OrganizationMembership.created_at.asc())).scalars().first()
    if membership is None:
        raise PermissionDenied("You do not have access to that workspace.")
    return membership


def get_auth_context(
    request: Request,
    db: DbSession,
    x_organization_id: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
) -> AuthContext:
    user = get_current_user(request, db)
    membership = _resolve_membership(db, user, x_organization_id)
    organization = db.get(Organization, membership.organization_id)
    if organization is None or organization.deleted_at is not None:
        raise PermissionDenied("Workspace unavailable.")
    return AuthContext(user=user, organization=organization, role=membership.role)


Ctx = Annotated[AuthContext, Depends(get_auth_context)]


def require_write(ctx: Ctx) -> AuthContext:
    if not ctx.can_write():
        raise PermissionDenied("Your workspace role is read-only.")
    return ctx


WriteCtx = Annotated[AuthContext, Depends(require_write)]


def require_admin(ctx: Ctx) -> AuthContext:
    if not ctx.is_admin():
        raise PermissionDenied("Administrator role required.")
    return ctx


AdminCtx = Annotated[AuthContext, Depends(require_admin)]


# --------------------------------------------------------------------------- CSRF
def verify_csrf(request: Request) -> None:
    """Double-submit cookie check for cookie-authenticated mutations.

    Bearer-token clients (CLI, tests, workers) are exempt because they do not rely
    on ambient browser credentials.
    """
    if settings.is_test or not settings.csrf_enabled:
        return
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return
    # Only enforce when the request is actually cookie authenticated.
    if not request.cookies.get(settings.cookie_name):
        return
    if not csrf_tokens_match(
        request.cookies.get(settings.cookie_csrf_name), request.headers.get("x-csrf-token")
    ):
        raise PermissionDenied("Missing or invalid CSRF token.", code="csrf_failed")


def role_at_least(role: MembershipRole, minimum: MembershipRole) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


__all__ = [
    "AdminCtx",
    "AuthContext",
    "Ctx",
    "CurrentUser",
    "DbSession",
    "WriteCtx",
    "get_auth_context",
    "get_current_user",
    "login_rate_limiter",
    "rate_limit",
    "require_admin",
    "require_write",
    "role_at_least",
    "verify_csrf",
]

_ = (Callable, PermissionDenied)  # re-exported types kept importable for type checkers
