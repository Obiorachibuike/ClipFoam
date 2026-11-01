"""Provider-independent social publishing.

Only platforms with a documented, official API and an authorization flow that
can be completed without special partner approval are implemented here. Anything
else is declared as a manual export, with its limitations spelled out — the UI
shows exactly what is supported instead of faking a connection.

Implemented: YouTube (Data API v3, resumable upload, OAuth2) and Mastodon
(Mastodon API, OAuth2). Both require operator-provided OAuth applications; when
credentials are absent the provider reports ``needs_configuration`` and the app
falls back to the manual export workflow.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import httpx
import jwt

from app.config import settings

logger = logging.getLogger(__name__)

MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
}


class PublishError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool = False, raw: dict | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable
        self.raw = raw or {}


class PublishNotConfigured(PublishError):
    def __init__(self, message: str, *, code: str = "publish_not_configured"):
        super().__init__(message, code=code, retryable=False)


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]
    external_account_id: str
    display_name: str
    avatar_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishPayload:
    title: str
    body: str
    hashtags: list[str]
    media_path: Path | None
    platform_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    external_id: str
    url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class SocialPublisher(Protocol):
    id: str

    def capabilities(self) -> dict[str, Any]: ...

    def status(self) -> tuple[str, str | None]: ...

    def authorization_url(self, *, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, *, code: str, redirect_uri: str) -> TokenBundle: ...

    def refresh(self, access_token: str, refresh_token: str | None) -> TokenBundle | None: ...

    def publish(self, *, access_token: str, payload: PublishPayload) -> PublishResult: ...

    def fetch_metrics(self, *, access_token: str, external_id: str) -> dict[str, float]: ...


def _media_type(path: Path | None) -> str | None:
    if path is None:
        return None
    return MIME_BY_SUFFIX.get(path.suffix.lower())


# --------------------------------------------------------------------------- YouTube
class YouTubePublisher:
    id = "youtube"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]

    def capabilities(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": "YouTube",
            "supports_oauth": True,
            "supported_media": ["video"],
            "supported_formats": ["mp4 (H.264/AAC)", "mov", "webm"],
            "required_permissions": ["youtube.upload", "youtube.readonly"],
            "upload_restrictions": [
                "Max 256 GB or 12 hours per video",
                "Unverified API projects upload as private until audited by Google",
            ],
            "publishing_limitations": [
                "API uploads default to private for unaudited projects",
                "Shorts are created automatically for vertical videos under 3 minutes",
            ],
            "supports_status_check": True,
        }

    def status(self) -> tuple[str, str | None]:
        if settings.youtube_client_id and settings.youtube_client_secret:
            return "available", None
        return (
            "needs_configuration",
            "Set CF_YOUTUBE_CLIENT_ID and CF_YOUTUBE_CLIENT_SECRET with a Google Cloud OAuth "
            "client that has the YouTube Data API v3 enabled.",
        )

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        http_status, detail = self.status()
        if http_status != "available":
            raise PublishNotConfigured(detail or "YouTube is not configured")
        params = httpx.QueryParams(
            {
                "client_id": settings.youtube_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
                "state": state,
            }
        )
        return f"{self.AUTH_URL}?{params}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> TokenBundle:
        token = self._token_request(
            {
                "code": code,
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        channel = self._fetch_channel(token["access_token"])
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600)))
            if token.get("expires_in")
            else None
        )
        return TokenBundle(
            access_token=token["access_token"],
            refresh_token=token.get("refresh_token"),
            expires_at=expires_at,
            scopes=str(token.get("scope", "")).split(),
            external_account_id=channel["id"],
            display_name=channel["title"],
            avatar_url=channel.get("thumbnail"),
            metadata={"channel_id": channel["id"], "handle": channel.get("custom_url")},
        )

    def refresh(self, access_token: str, refresh_token: str | None) -> TokenBundle | None:
        if not refresh_token:
            return None
        token = self._token_request(
            {
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        )
        channel = self._fetch_channel(token["access_token"])
        return TokenBundle(
            access_token=token["access_token"],
            refresh_token=refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in", 3600))),
            scopes=[],
            external_account_id=channel["id"],
            display_name=channel["title"],
            avatar_url=channel.get("thumbnail"),
        )

    def _token_request(self, payload: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(self.TOKEN_URL, data=payload)
        except httpx.HTTPError as exc:
            raise PublishError(f"YouTube token endpoint unreachable: {exc}", code="oauth_unreachable", retryable=True) from exc
        if response.status_code >= 400:
            raise PublishError(
                f"YouTube rejected the token request (HTTP {response.status_code}): {response.text[:300]}",
                code="oauth_rejected",
            )
        return response.json()

    def _fetch_channel(self, access_token: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={"part": "snippet", "mine": "true"},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise PublishError(f"YouTube API unreachable: {exc}", code="api_unreachable", retryable=True) from exc
        if response.status_code == 401:
            raise PublishError("YouTube access token was rejected", code="token_invalid")
        if response.status_code >= 400:
            raise PublishError(
                f"YouTube channel lookup failed (HTTP {response.status_code})", code="channel_lookup_failed"
            )
        items = response.json().get("items") or []
        if not items:
            raise PublishError(
                "No YouTube channel is attached to this Google account", code="no_channel"
            )
        snippet = items[0].get("snippet", {})
        thumbs = snippet.get("thumbnails") or {}
        return {
            "id": items[0]["id"],
            "title": snippet.get("title", "YouTube channel"),
            "custom_url": snippet.get("customUrl"),
            "thumbnail": (thumbs.get("default") or {}).get("url"),
        }

    def publish(self, *, access_token: str, payload: PublishPayload) -> PublishResult:
        if payload.media_path is None or not payload.media_path.exists():
            raise PublishError("A rendered video file is required to publish to YouTube", code="missing_media")
        size = payload.media_path.stat().st_size
        description = payload.body
        if payload.hashtags:
            description = f"{description}\n\n{' '.join(payload.hashtags)}".strip()
        metadata = {
            "snippet": {
                "title": payload.title[:100] or "ClipFoam clip",
                "description": description[:4900],
                "tags": [tag.lstrip("#") for tag in payload.hashtags][:20],
                "categoryId": str(payload.platform_metadata.get("category_id", "22")),
            },
            "status": {
                "privacyStatus": payload.platform_metadata.get("privacy_status", "private"),
                "selfDeclaredMadeForKids": bool(payload.platform_metadata.get("made_for_kids", False)),
            },
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": _media_type(payload.media_path) or "video/mp4",
        }
        try:
            with httpx.Client(timeout=60) as client:
                init = client.post(
                    "https://www.googleapis.com/upload/youtube/v3/videos",
                    params={"uploadType": "resumable", "part": "snippet,status"},
                    headers=headers,
                    json=metadata,
                )
        except httpx.HTTPError as exc:
            raise PublishError(f"YouTube upload could not start: {exc}", code="upload_unreachable", retryable=True) from exc
        if init.status_code == 401:
            raise PublishError("YouTube access token expired", code="token_expired")
        if init.status_code >= 400:
            raise PublishError(
                f"YouTube rejected the upload (HTTP {init.status_code}): {init.text[:400]}",
                code="upload_rejected",
                raw={"response": init.text[:1000]},
            )
        upload_url = init.headers.get("location")
        if not upload_url:
            raise PublishError("YouTube did not return an upload session URL", code="no_upload_session")
        try:
            with httpx.Client(timeout=settings.render_timeout_seconds) as client:
                with payload.media_path.open("rb") as handle:
                    upload = client.put(
                        upload_url,
                        content=handle,
                        headers={
                            "Content-Length": str(size),
                            "Content-Type": _media_type(payload.media_path) or "video/mp4",
                        },
                    )
        except httpx.HTTPError as exc:
            raise PublishError(f"YouTube upload failed mid-transfer: {exc}", code="upload_failed", retryable=True) from exc
        if upload.status_code >= 400:
            raise PublishError(
                f"YouTube upload failed (HTTP {upload.status_code}): {upload.text[:400]}",
                code="upload_failed",
                retryable=upload.status_code >= 500,
            )
        data = upload.json()
        video_id = data.get("id")
        if not video_id:
            raise PublishError("YouTube did not confirm the video id", code="upload_unconfirmed")
        return PublishResult(
            external_id=video_id,
            url=f"https://www.youtube.com/watch?v={video_id}",
            raw={
                "status": (data.get("status") or {}).get("uploadStatus"),
                "privacy": (data.get("status") or {}).get("privacyStatus"),
            },
        )

    def fetch_metrics(self, *, access_token: str, external_id: str) -> dict[str, float]:
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "statistics", "id": external_id},
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise PublishError(f"YouTube metrics unavailable: {exc}", code="metrics_unreachable") from exc
        if response.status_code == 403:
            raise PublishError("YouTube read scope not granted for metrics", code="permission_missing")
        if response.status_code >= 400:
            raise PublishError(f"YouTube metrics failed (HTTP {response.status_code})", code="metrics_failed")
        items = response.json().get("items") or []
        if not items:
            return {}
        stats = items[0].get("statistics", {})
        return {
            key: float(value)
            for key, value in stats.items()
            if isinstance(value, (int, float, str)) and str(value).replace(".", "", 1).isdigit()
        }


# --------------------------------------------------------------------------- Mastodon
class MastodonPublisher:
    id = "mastodon"

    def capabilities(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": "Mastodon",
            "supports_oauth": True,
            "supported_media": ["video", "audio"],
            "supported_formats": ["mp4", "webm", "mov", "mp3"],
            "required_permissions": ["read", "write"],
            "upload_restrictions": [
                f"Server instance limit: {settings.mastodon_instance}",
                "Video processing may take a moment before the post appears",
            ],
            "publishing_limitations": [
                "Character limit is instance dependent (default 500)",
                "Media is attached asynchronously; ClipFoam waits for the media id",
            ],
            "supports_status_check": True,
        }

    def status(self) -> tuple[str, str | None]:
        if settings.mastodon_client_id and settings.mastodon_client_secret:
            return "available", f"Instance: {settings.mastodon_instance}"
        return (
            "needs_configuration",
            "Register an OAuth application on your Mastodon instance (Development → New application, "
            "scopes read+write) and set CF_MASTODON_CLIENT_ID / CF_MASTODON_CLIENT_SECRET / "
            "CF_MASTODON_INSTANCE.",
        )

    def authorization_url(self, *, state: str, redirect_uri: str) -> str:
        status, detail = self.status()
        if status != "available":
            raise PublishNotConfigured(detail or "Mastodon is not configured")
        params = httpx.QueryParams(
            {
                "client_id": settings.mastodon_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "read write",
                "state": state,
            }
        )
        return f"{settings.mastodon_instance.rstrip('/')}/oauth/authorize?{params}"

    def exchange_code(self, *, code: str, redirect_uri: str) -> TokenBundle:
        payload = {
            "client_id": settings.mastodon_client_id,
            "client_secret": settings.mastodon_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code": code,
            "scope": "read write",
        }
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(f"{settings.mastodon_instance.rstrip('/')}/oauth/token", json=payload)
        except httpx.HTTPError as exc:
            raise PublishError(f"Mastodon token endpoint unreachable: {exc}", code="oauth_unreachable", retryable=True) from exc
        if response.status_code >= 400:
            raise PublishError(
                f"Mastodon rejected the token request (HTTP {response.status_code}): {response.text[:300]}",
                code="oauth_rejected",
            )
        token = response.json()
        account = self._verify(token["access_token"])
        return TokenBundle(
            access_token=token["access_token"],
            refresh_token=None,
            expires_at=None,
            scopes=token.get("scope", "").split(),
            external_account_id=str(account.get("id")),
            display_name=account.get("display_name") or account.get("username") or "Mastodon",
            avatar_url=account.get("avatar"),
            metadata={"acct": account.get("acct"), "instance": settings.mastodon_instance},
        )

    def refresh(self, access_token: str, refresh_token: str | None) -> TokenBundle | None:
        return None

    def _verify(self, access_token: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(
                    f"{settings.mastodon_instance.rstrip('/')}/api/v1/accounts/verify_credentials",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise PublishError(f"Mastodon API unreachable: {exc}", code="api_unreachable", retryable=True) from exc
        if response.status_code == 401:
            raise PublishError("Mastodon token was rejected", code="token_invalid")
        if response.status_code >= 400:
            raise PublishError(f"Mastodon account lookup failed (HTTP {response.status_code})", code="account_lookup_failed")
        return response.json()

    def publish(self, *, access_token: str, payload: PublishPayload) -> PublishResult:
        base = settings.mastodon_instance.rstrip("/")
        media_ids: list[str] = []
        if payload.media_path is not None:
            mime = _media_type(payload.media_path) or "video/mp4"
            try:
                with httpx.Client(timeout=settings.render_timeout_seconds) as client:
                    with payload.media_path.open("rb") as handle:
                        response = client.post(
                            f"{base}/api/v2/media",
                            headers={"Authorization": f"Bearer {access_token}"},
                            files={"file": (payload.media_path.name, handle, mime)},
                            data={"description": payload.title[:1400]},
                        )
            except httpx.HTTPError as exc:
                raise PublishError(f"Mastodon media upload failed: {exc}", code="upload_failed", retryable=True) from exc
            if response.status_code == 401:
                raise PublishError("Mastodon access token expired", code="token_expired")
            if response.status_code >= 400:
                raise PublishError(
                    f"Mastodon rejected the media (HTTP {response.status_code}): {response.text[:300]}",
                    code="upload_rejected",
                )
            media = response.json()
            media_ids.append(str(media.get("id")))
            # 202 means the media is still processing; poll briefly for readiness.
            for _ in range(20):
                if media.get("url"):
                    break
                time.sleep(3)
                with httpx.Client(timeout=30) as client:
                    media = client.get(
                        f"{base}/api/v1/media/{media.get('id')}",
                        headers={"Authorization": f"Bearer {access_token}"},
                    ).json()
        text = payload.body.strip()
        if payload.hashtags:
            text = f"{text}\n\n{' '.join(payload.hashtags)}".strip()
        try:
            with httpx.Client(timeout=60) as client:
                status = client.post(
                    f"{base}/api/v1/statuses",
                    headers={"Authorization": f"Bearer {access_token}"},
                    data={"status": text[:4900], "media_ids[]": media_ids, "visibility": payload.platform_metadata.get("visibility", "public")},
                )
        except httpx.HTTPError as exc:
            raise PublishError(f"Mastodon publish failed: {exc}", code="publish_unreachable", retryable=True) from exc
        if status.status_code >= 400:
            raise PublishError(
                f"Mastodon rejected the post (HTTP {status.status_code}): {status.text[:300]}",
                code="publish_rejected",
                retryable=status.status_code >= 500,
            )
        data = status.json()
        return PublishResult(
            external_id=str(data.get("id")),
            url=data.get("url") or data.get("uri"),
            raw={"visibility": data.get("visibility")},
        )

    def fetch_metrics(self, *, access_token: str, external_id: str) -> dict[str, float]:
        base = settings.mastodon_instance.rstrip("/")
        try:
            with httpx.Client(timeout=30) as client:
                response = client.get(
                    f"{base}/api/v1/statuses/{external_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            raise PublishError(f"Mastodon metrics unavailable: {exc}", code="metrics_unreachable") from exc
        if response.status_code >= 400:
            raise PublishError(f"Mastodon metrics failed (HTTP {response.status_code})", code="metrics_failed")
        data = response.json()
        metrics = {}
        for key in ("favourites_count", "reblogs_count", "replies_count"):
            if isinstance(data.get(key), int):
                metrics[key] = float(data[key])
        return metrics


# --------------------------------------------------------------------------- registry
IMPLEMENTED: dict[str, SocialPublisher] = {
    "youtube": YouTubePublisher(),
    "mastodon": MastodonPublisher(),
}

MANUAL_ONLY: dict[str, dict[str, Any]] = {
    "tiktok": {
        "name": "TikTok",
        "why": "The Content Posting API requires an approved developer app, a verified domain and a "
        "direct-post audit. Without those credentials ClipFoam provides export + upload guidance.",
        "supported_formats": ["mp4 9:16"],
    },
    "instagram": {
        "name": "Instagram",
        "why": "Publishing requires a Business/Creator account linked to a Facebook Page plus Meta app review "
        "for instagram_content_publish.",
        "supported_formats": ["mp4 9:16", "mp4 4:5"],
    },
    "x": {
        "name": "X (Twitter)",
        "why": "API access is paid per tier and media upload requires elevated access.",
        "supported_formats": ["mp4 9:16", "mp4 16:9"],
    },
    "linkedin": {
        "name": "LinkedIn",
        "why": "Video publishing requires a LinkedIn Marketing Developer Platform app and page admin consent.",
        "supported_formats": ["mp4 1:1", "mp4 16:9"],
    },
    "facebook": {
        "name": "Facebook Pages",
        "why": "Requires App Review for pages_manage_posts and a linked Page.",
        "supported_formats": ["mp4 9:16", "mp4 1:1"],
    },
    "bluesky": {
        "name": "Bluesky",
        "why": "Video uploads are supported by the AT Protocol but need an app password scoped to the account; "
        "not enabled in this build.",
        "supported_formats": ["mp4"],
    },
}


def list_providers() -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []
    for provider in IMPLEMENTED.values():
        caps = provider.capabilities()
        status, detail = provider.status()
        providers.append(
            {
                "id": caps["id"],
                "name": caps["name"],
                "status": status,
                "supports_oauth": True,
                "supported_media": caps["supported_media"],
                "supported_formats": caps["supported_formats"],
                "required_permissions": caps["required_permissions"],
                "upload_restrictions": caps["upload_restrictions"],
                "publishing_limitations": caps["publishing_limitations"],
                "supports_status_check": caps["supports_status_check"],
                "notes": detail,
            }
        )
    for provider_id, info in MANUAL_ONLY.items():
        providers.append(
            {
                "id": provider_id,
                "name": info["name"],
                "status": "manual_export",
                "supports_oauth": False,
                "supported_media": ["video"],
                "supported_formats": info["supported_formats"],
                "required_permissions": [],
                "upload_restrictions": ["Not implemented in this build"],
                "publishing_limitations": [info["why"]],
                "supports_status_check": False,
                "notes": info["why"],
            }
        )
    return providers


def get_publisher(platform: str) -> SocialPublisher:
    provider = IMPLEMENTED.get(platform)
    if provider is None:
        raise PublishNotConfigured(
            f"{platform} publishing is not available in this build. Download the export and upload "
            "manually, or connect YouTube/Mastodon.",
            code="platform_not_supported",
        )
    return provider


def provider_requires_configuration(platform: str) -> str | None:
    provider = IMPLEMENTED.get(platform)
    if provider is None:
        return f"{platform} is manual-export only"
    status, detail = provider.status()
    return None if status == "available" else detail


# --------------------------------------------------------------------------- OAuth state
STATE_TTL_MINUTES = 15


def create_oauth_state(*, organization_id: uuid.UUID, user_id: uuid.UUID, platform: str) -> str:
    payload = {
        "org": str(organization_id),
        "sub": str(user_id),
        "platform": platform,
        "nonce": uuid.uuid4().hex,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES),
        "typ": "oauth_state",
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def read_oauth_state(state: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise PublishError("The authorization link expired; start the connection again.", code="oauth_state_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise PublishError("Invalid authorization state", code="oauth_state_invalid") from exc
    if payload.get("typ") != "oauth_state":
        raise PublishError("Invalid authorization state", code="oauth_state_invalid")
    return payload


def redirect_uri_for(platform: str) -> str:
    base = settings.oauth_redirect_base_url.rstrip("/")
    return f"{base}{settings.api_prefix}/social/callback/{platform}"


def debug_token_shape(token: str | None) -> str:
    """Never log tokens; used for diagnostics only."""
    if not token:
        return "missing"
    return f"len={len(token)}"


_ = (json, os)
