"""Reusable brand kits (palette, fonts, caption preset, logo)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Request, UploadFile
from sqlalchemy import select

from app.deps import Ctx, DbSession, WriteCtx, verify_csrf
from app.errors import NotFoundError, UnsupportedMedia, ValidationError
from app.models import BrandKit, VisualPreset
from app.schemas import BrandKitCreate, BrandKitOut, BrandKitUpdate, LogoUploadResponse
from app.services.audit import record_audit
from app.api.serializers import brand_kit_out
from app.storage import build_key, get_storage

router = APIRouter(prefix="/brand-kits", tags=["brand-kits"])

ALLOWED_LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def get_brand_kit(db, org_id: uuid.UUID, kit_id: uuid.UUID) -> BrandKit:
    kit = db.execute(
        select(BrandKit).where(
            BrandKit.id == kit_id,
            BrandKit.organization_id == org_id,
            BrandKit.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if kit is None:
        raise NotFoundError("Brand kit not found", code="brand_kit_not_found")
    return kit


@router.get("", response_model=list[BrandKitOut])
def list_brand_kits(db: DbSession, ctx: Ctx) -> list[BrandKitOut]:
    kits = (
        db.execute(
            select(BrandKit)
            .where(BrandKit.organization_id == ctx.org_id, BrandKit.deleted_at.is_(None))
            .order_by(BrandKit.is_default.desc(), BrandKit.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [brand_kit_out(kit) for kit in kits]


@router.post("", response_model=BrandKitOut, status_code=201)
def create_brand_kit(
    payload: BrandKitCreate, request: Request, db: DbSession, ctx: WriteCtx
) -> BrandKitOut:
    verify_csrf(request)
    if payload.is_default:
        for kit in db.execute(
            select(BrandKit).where(BrandKit.organization_id == ctx.org_id)
        ).scalars():
            kit.is_default = False
    kit = BrandKit(
        organization_id=ctx.org_id,
        name=payload.name,
        primary_color=payload.primary_color,
        secondary_color=payload.secondary_color,
        text_color=payload.text_color,
        highlight_color=payload.highlight_color,
        font_family=payload.font_family,
        heading_font=payload.heading_font,
        caption_preset=payload.caption_preset,
        caption_style=payload.caption_style,
        intro_text=payload.intro_text,
        outro_text=payload.outro_text,
        is_default=payload.is_default,
    )
    db.add(kit)
    record_audit(
        db,
        action="brand_kit.created",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="brand_kit",
        target_id=str(kit.id),
        request=request,
    )
    db.commit()
    return brand_kit_out(kit)


@router.get("/presets")
def list_caption_presets() -> list[dict[str, object]]:
    from app.services.captions import PRESETS

    return [
        {"id": preset.value, "defaults": defaults}
        for preset, defaults in PRESETS.items()
    ]


@router.get("/{kit_id}", response_model=BrandKitOut)
def read_brand_kit(kit_id: uuid.UUID, db: DbSession, ctx: Ctx) -> BrandKitOut:
    return brand_kit_out(get_brand_kit(db, ctx.org_id, kit_id))


@router.patch("/{kit_id}", response_model=BrandKitOut)
def update_brand_kit(
    kit_id: uuid.UUID, payload: BrandKitUpdate, request: Request, db: DbSession, ctx: WriteCtx
) -> BrandKitOut:
    verify_csrf(request)
    kit = get_brand_kit(db, ctx.org_id, kit_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("is_default"):
        for other in db.execute(
            select(BrandKit).where(
                BrandKit.organization_id == ctx.org_id, BrandKit.id != kit.id
            )
        ).scalars():
            other.is_default = False
    for field, value in data.items():
        setattr(kit, field, value)
    record_audit(
        db,
        action="brand_kit.updated",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="brand_kit",
        target_id=str(kit.id),
        request=request,
    )
    db.commit()
    return brand_kit_out(kit)


@router.delete("/{kit_id}", status_code=204, response_model=None)
def delete_brand_kit(kit_id: uuid.UUID, request: Request, db: DbSession, ctx: WriteCtx) -> None:
    verify_csrf(request)
    kit = get_brand_kit(db, ctx.org_id, kit_id)
    if kit.logo_storage_key:
        try:
            get_storage().delete(kit.logo_storage_key)
        except Exception:  # noqa: BLE001
            pass
    db.delete(kit)
    record_audit(
        db,
        action="brand_kit.deleted",
        organization_id=ctx.org_id,
        actor_user_id=ctx.user.id,
        target_type="brand_kit",
        target_id=str(kit_id),
        request=request,
    )
    db.commit()
    return None


@router.post("/{kit_id}/logo", response_model=LogoUploadResponse, status_code=201)
async def upload_logo(
    kit_id: uuid.UUID,
    request: Request,
    db: DbSession,
    ctx: WriteCtx,
    file: UploadFile = File(...),
) -> LogoUploadResponse:
    verify_csrf(request)
    kit = get_brand_kit(db, ctx.org_id, kit_id)
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    extension = ALLOWED_LOGO_TYPES.get(content_type)
    if extension is None:
        raise UnsupportedMedia(
            "Logo must be a PNG, JPEG or WebP image", code="unsupported_logo_type"
        )
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise ValidationError("Logo must be smaller than 5 MB", code="logo_too_large")
    if not data:
        raise ValidationError("Logo upload was empty", code="empty_logo")
    if extension == "png" and not data.startswith(b"\x89PNG"):
        raise UnsupportedMedia("File is not a valid PNG", code="invalid_logo_signature")
    if extension == "jpg" and not data.startswith(b"\xff\xd8"):
        raise UnsupportedMedia("File is not a valid JPEG", code="invalid_logo_signature")
    key = build_key("orgs", str(ctx.org_id), "brand", str(kit.id), f"logo.{extension}")
    get_storage().save_bytes(data, key, content_type)
    if kit.logo_storage_key and kit.logo_storage_key != key:
        try:
            get_storage().delete(kit.logo_storage_key)
        except Exception:  # noqa: BLE001
            pass
    kit.logo_storage_key = key
    db.commit()
    return LogoUploadResponse(logo_url=get_storage().signed_url(key))


_ = VisualPreset
