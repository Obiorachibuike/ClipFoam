"""Authentication and account management."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.deps import Ctx, CurrentUser, DbSession, get_current_user, login_rate_limiter, verify_csrf
from app.errors import ConflictError, PermissionDenied, Unauthorized, ValidationError
from app.models import MembershipRole, Organization, OrganizationMembership, User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    MembershipOut,
    OrganizationOut,
    RegisterRequest,
    SessionOut,
    UpdateOrganizationRequest,
    UpdateProfileRequest,
    UserOut,
)
from app.security import (
    create_access_token,
    hash_password,
    new_csrf_token,
    verify_password,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _slugify(value: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:40] or "workspace"
    return f"{base}-{uuid.uuid4().hex[:6]}"


def _session_payload(db, user: User, organization: Organization, role: MembershipRole) -> SessionOut:
    memberships = (
        db.execute(
            select(OrganizationMembership)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .where(
                OrganizationMembership.user_id == user.id,
                Organization.deleted_at.is_(None),
            )
            .order_by(OrganizationMembership.created_at)
        )
        .scalars()
        .all()
    )
    return SessionOut(
        user=UserOut.model_validate(user),
        organization=OrganizationOut.model_validate(organization),
        memberships=[
            MembershipOut(
                organization_id=membership.organization_id,
                role=membership.role,
                organization=OrganizationOut.model_validate(membership.organization),
            )
            for membership in memberships
        ],
        role=role,
        csrf_token="",
        limits={},
    )


def _set_session_cookies(response: Response, token: str, csrf: str) -> None:
    max_age = settings.access_token_ttl_minutes * 60
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )
    response.set_cookie(
        settings.cookie_csrf_name,
        csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )


@router.post("/register", response_model=SessionOut, status_code=201)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: DbSession,
) -> SessionOut:
    if not settings.enable_signup:
        raise PermissionDenied("Sign-ups are disabled for this deployment.", code="signup_disabled")
    verify_csrf(request)
    existing = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("An account with this email already exists.", code="email_taken")
    user = User(
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        name=payload.name.strip(),
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:  # pragma: no cover - race on unique index
        db.rollback()
        raise ConflictError("An account with this email already exists.", code="email_taken") from exc
    organization = Organization(
        name=(payload.organization_name or f"{user.name.split()[0]}'s workspace")[:160],
        slug=_slugify(payload.organization_name or user.name),
    )
    db.add(organization)
    db.flush()
    membership = OrganizationMembership(
        organization_id=organization.id, user_id=user.id, role=MembershipRole.OWNER
    )
    db.add(membership)
    record_audit(
        db,
        action="auth.register",
        organization_id=organization.id,
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    db.commit()
    token, _expires = create_access_token(
        user_id=user.id, password_hash=user.password_hash, org_id=organization.id
    )
    csrf = new_csrf_token()
    _set_session_cookies(response, token, csrf)
    session = _session_payload(db, user, organization, membership.role)
    session.csrf_token = csrf
    return session


@router.post("/login", response_model=SessionOut, dependencies=[Depends(login_rate_limiter)])
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession) -> SessionOut:
    verify_csrf(request)
    user = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()
    if user is None or user.deleted_at is not None or not verify_password(payload.password, user.password_hash):
        record_audit(
            db,
            action="auth.login_failed",
            target_type="user",
            target_id=str(user.id) if user else None,
            request=request,
        )
        db.commit()
        raise Unauthorized("Invalid email or password.", code="invalid_credentials")
    if not user.is_active:
        raise Unauthorized("This account has been disabled.", code="account_disabled")
    membership = (
        db.execute(
            select(OrganizationMembership)
            .join(Organization, Organization.id == OrganizationMembership.organization_id)
            .where(
                OrganizationMembership.user_id == user.id,
                Organization.deleted_at.is_(None),
            )
            .order_by(OrganizationMembership.created_at)
        )
        .scalars()
        .first()
    )
    if membership is None:
        raise Unauthorized("This account has no workspace.", code="no_workspace")
    organization = db.get(Organization, membership.organization_id)
    if organization is None:
        raise Unauthorized("Workspace unavailable.", code="no_workspace")
    user.last_login_at = datetime.now(timezone.utc)
    token, _expires = create_access_token(
        user_id=user.id, password_hash=user.password_hash, org_id=organization.id
    )
    csrf = new_csrf_token()
    _set_session_cookies(response, token, csrf)
    record_audit(
        db,
        action="auth.login",
        organization_id=organization.id,
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        request=request,
    )
    db.commit()
    session = _session_payload(db, user, organization, membership.role)
    session.csrf_token = csrf
    return session


@router.post("/logout", status_code=204, response_model=None)
def logout(request: Request, response: Response, db: DbSession, user: CurrentUser) -> Response:
    verify_csrf(request)
    record_audit(db, action="auth.logout", actor_user_id=user.id, target_type="user", target_id=str(user.id), request=request)
    db.commit()
    response.delete_cookie(settings.cookie_name, path="/", domain=settings.cookie_domain)
    response.delete_cookie(settings.cookie_csrf_name, path="/", domain=settings.cookie_domain)
    response.status_code = 204
    return response


@router.get("/session", response_model=SessionOut)
def read_session(
    db: DbSession,
    user: Annotated[User, Depends(get_current_user)],
    response: Response,
    x_organization_id: Annotated[str | None, None] = None,
) -> SessionOut:
    org_uuid: uuid.UUID | None = None
    if x_organization_id:
        try:
            org_uuid = uuid.UUID(x_organization_id)
        except ValueError:
            org_uuid = None
    stmt = (
        select(OrganizationMembership)
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            OrganizationMembership.user_id == user.id,
            Organization.deleted_at.is_(None),
        )
        .order_by(OrganizationMembership.created_at)
    )
    if org_uuid is not None:
        stmt = stmt.where(OrganizationMembership.organization_id == org_uuid)
    membership = db.execute(stmt).scalars().first()
    if membership is None:
        raise Unauthorized("No workspace found for this account.", code="no_workspace")
    organization = db.get(Organization, membership.organization_id)
    if organization is None:
        raise Unauthorized("No workspace found for this account.", code="no_workspace")
    csrf = new_csrf_token()
    response.set_cookie(
        settings.cookie_csrf_name,
        csrf,
        max_age=settings.access_token_ttl_minutes * 60,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        domain=settings.cookie_domain,
        path="/",
    )
    session = _session_payload(db, user, organization, membership.role)
    session.csrf_token = csrf
    session.limits = {
        "max_upload_mb": settings.max_upload_mb,
        "max_duration_seconds": settings.max_duration_seconds,
        "transcription_provider": settings.transcription_provider,
        "ai_provider": settings.ai_provider,
        "storage_backend": settings.storage_backend,
    }
    return session


@router.patch("/profile", response_model=UserOut)
def update_profile(
    payload: UpdateProfileRequest, request: Request, db: DbSession, user: CurrentUser
) -> UserOut:
    verify_csrf(request)
    if payload.name is not None:
        user.name = payload.name.strip()
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url or None
    record_audit(db, action="user.profile_updated", actor_user_id=user.id, target_type="user", target_id=str(user.id), request=request)
    db.commit()
    return UserOut.model_validate(user)


@router.post("/password", status_code=204, response_model=None)
def change_password(
    payload: ChangePasswordRequest, request: Request, response: Response, db: DbSession, user: CurrentUser
) -> Response:
    verify_csrf(request)
    if not verify_password(payload.current_password, user.password_hash):
        raise ValidationError("Current password is incorrect.", code="invalid_password")
    user.password_hash = hash_password(payload.new_password)
    # Re-issue the session so the current device is not logged out by the pwv check.
    token, _ = create_access_token(
        user_id=user.id,
        password_hash=user.password_hash,
        org_id=user.memberships[0].organization_id if user.memberships else None,
    )
    csrf = new_csrf_token()
    _set_session_cookies(response, token, csrf)
    record_audit(db, action="user.password_changed", actor_user_id=user.id, target_type="user", target_id=str(user.id), request=request)
    db.commit()
    response.status_code = 204
    return response


@router.patch("/organization", response_model=OrganizationOut)
def update_organization(
    payload: UpdateOrganizationRequest, request: Request, db: DbSession, ctx: Ctx
) -> OrganizationOut:
    verify_csrf(request)
    if not ctx.is_admin():
        raise PermissionDenied("Only workspace admins can rename the workspace.")
    ctx.organization.name = payload.name.strip()
    record_audit(
        db,
        action="organization.renamed",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="organization",
        target_id=str(ctx.org_id),
        request=request,
    )
    db.commit()
    return OrganizationOut.model_validate(ctx.organization)
