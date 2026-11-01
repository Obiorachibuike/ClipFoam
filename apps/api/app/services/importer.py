"""URL import with SSRF protection.

Only direct media URLs from public hosts are accepted. Private/loopback/
link-local destinations are rejected before any connection is made, redirects
are re-validated hop by hop, and the download is capped by size and time. There
is no general-purpose proxy here: the response is written to storage as media.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_MIME_PREFIXES = ("text/html", "application/xhtml", "application/json")
MAX_REDIRECTS = 3


class ImportRejected(ValueError):
    """The URL is not safe or not a supported media source."""


class ImportFailed(RuntimeError):
    """The remote host could not deliver the media."""


@dataclass
class ImportResult:
    path: Path
    content_type: str | None
    size_bytes: int
    final_url: str


def _resolve_addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ImportFailed(f"Unable to resolve host '{host}': {exc}") from exc
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise ImportFailed(f"Host '{host}' did not resolve to any address")
    return addresses


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def validate_import_url(raw_url: str) -> str:
    if not raw_url or len(raw_url) > 2000:
        raise ImportRejected("URL is missing or too long")
    parsed = urlparse(raw_url.strip())
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ImportRejected("Only http(s) URLs are supported")
    if parsed.username or parsed.password:
        raise ImportRejected("URLs with embedded credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise ImportRejected("URL has no host")
    if host.lower() in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        raise ImportRejected("Local and cloud-metadata hosts are blocked")
    allowlist = settings.import_host_allowlist
    if allowlist and not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in allowlist
    ):
        raise ImportRejected(
            "This host is not in CF_IMPORT_ALLOWED_HOSTS. Add it to permit imports from this source."
        )
    if not settings.allow_private_import_networks:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        for address in _resolve_addresses(host, port):
            if not _is_public(address):
                raise ImportRejected(f"Destination {address} is a private or reserved network address")
    return raw_url.strip()


def download_to_file(url: str, destination: Path, *, max_bytes: int | None = None) -> ImportResult:
    """Download a media URL to ``destination`` with hop-by-hop validation."""
    if not settings.allow_url_import:
        raise ImportRejected("URL import is disabled by configuration (CF_ALLOW_URL_IMPORT=false)")
    limit = max_bytes or settings.import_max_bytes
    current = validate_import_url(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    timeout = httpx.Timeout(settings.import_timeout_seconds, connect=20.0)
    headers = {"User-Agent": "ClipFoam/1.0 (+https://github.com/Obiorachibuike/ClipFoam)"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, headers=headers) as client:
            for _hop in range(MAX_REDIRECTS + 1):
                with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ImportFailed("Redirect without a Location header")
                        current = validate_import_url(str(httpx.URL(current).join(location)))
                        continue
                    if response.status_code >= 400:
                        raise ImportFailed(f"Remote server returned HTTP {response.status_code}")
                    content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
                    if any(content_type.startswith(prefix) for prefix in BLOCKED_MIME_PREFIXES):
                        raise ImportRejected(
                            f"URL returned '{content_type}'. Direct authorization-free media URLs are "
                            "required — platform page URLs are not downloadable."
                        )
                    declared = response.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > limit:
                        raise ImportRejected(
                            f"Remote file is larger than the {limit // (1024 * 1024)} MB import limit"
                        )
                    with destination.open("wb") as handle:
                        for chunk in response.iter_bytes(1024 * 256):
                            written += len(chunk)
                            if written > limit:
                                handle.close()
                                destination.unlink(missing_ok=True)
                                raise ImportRejected(
                                    f"Download exceeded the {limit // (1024 * 1024)} MB import limit"
                                )
                            handle.write(chunk)
                    return ImportResult(
                        path=destination,
                        content_type=content_type or None,
                        size_bytes=written,
                        final_url=current,
                    )
            raise ImportFailed("Too many redirects")
    except httpx.TimeoutException as exc:
        raise ImportFailed(
            f"Download timed out after {settings.import_timeout_seconds}s"
        ) from exc
    except httpx.HTTPError as exc:
        raise ImportFailed(f"Download failed: {exc}") from exc


def source_host(url: str) -> str:
    return urlparse(url).hostname or "unknown"
