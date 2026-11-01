"""Typed application errors with stable machine-readable codes."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error carrying an HTTP status and a stable error code."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class PermissionDenied(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class RateLimitError(AppError):
    status_code = 429
    code = "rate_limited"


class QuotaExceeded(AppError):
    status_code = 413
    code = "quota_exceeded"


class UnsupportedMedia(AppError):
    status_code = 415
    code = "unsupported_media"


class ProviderUnavailable(AppError):
    """An external AI/transcription/publishing provider is missing or failing."""

    status_code = 503
    code = "provider_unavailable"


class PublishError(AppError):
    status_code = 502
    code = "publish_failed"
