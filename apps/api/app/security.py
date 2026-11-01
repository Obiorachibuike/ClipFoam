"""Password hashing, session tokens, CSRF, secret encryption, signed file URLs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

ALGORITHM = "HS256"


# --------------------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def password_fingerprint(password_hash: str) -> str:
    """Short non-reversible marker so token revocation works after a password change."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- sessions
def create_access_token(*, user_id: uuid.UUID, password_hash: str, org_id: uuid.UUID | None) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_ttl_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(org_id) if org_id else None,
        "pwv": password_fingerprint(password_hash),
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_urlsafe(12),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return token, expires


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(
        token,
        settings.secret_key,
        algorithms=[ALGORITHM],
        options={"require": ["sub", "exp"]},
    )


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_tokens_match(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


# --------------------------------------------------------------------------- encryption
def _fernet() -> Fernet:
    raw = settings.token_encryption_key
    if raw:
        key_bytes = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    else:
        # Derive a stable key from the app secret so a single secret is enough to boot.
        key_bytes = base64.urlsafe_b64encode(
            hashlib.sha256(f"clipfoam-token-key::{settings.secret_key}".encode()).digest()
        )
    return Fernet(key_bytes)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:  # pragma: no cover - configuration failure path
        raise ValueError("Unable to decrypt stored credential (secret key changed?)") from exc


# --------------------------------------------------------------------------- signed URLs
def sign_storage_key(key: str, *, ttl_seconds: int | None = None) -> dict[str, int | str]:
    expires = int(time.time()) + (ttl_seconds or settings.signed_url_ttl_seconds)
    signature = hmac.new(
        settings.secret_key.encode("utf-8"), f"{key}:{expires}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {"expires": expires, "signature": signature}


def verify_storage_signature(key: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    expected = hmac.new(
        settings.secret_key.encode("utf-8"), f"{key}:{expires}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def hash_idempotency(*parts: str) -> str:
    return hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
