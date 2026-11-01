"""Object storage abstraction: local disk (dev) and S3-compatible buckets (prod).

Buckets are private in both modes:
* S3/R2 objects are only reachable through short-lived presigned URLs.
* Local objects are only reachable through the API's signed ``/files`` endpoint.
"""

from __future__ import annotations

import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import quote

from app.config import settings
from app.security import sign_storage_key


class StorageError(RuntimeError):
    pass


class Storage(ABC):
    @abstractmethod
    def save_stream(self, file_obj, key: str, content_type: str) -> int: ...

    @abstractmethod
    def save_bytes(self, data: bytes, key: str, content_type: str) -> int: ...

    @abstractmethod
    def open_local(self, key: str) -> Path:
        """Return a local filesystem path for processing (downloading if needed)."""

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def signed_url(self, key: str, *, ttl_seconds: int | None = None) -> str: ...

    @abstractmethod
    def release_local(self, path: Path) -> None:
        """Discard a temporary local copy produced by ``open_local``."""


def build_key(*parts: str) -> str:
    """Namespaced object key, e.g. ``orgs/<org>/projects/<p>/media/<uuid>.mp4``."""
    safe = [quote(str(part), safe="") for part in parts]
    return "/".join(safe)


class LocalStorage(Storage):
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise StorageError("Invalid storage key")
        return target

    def save_stream(self, file_obj, key: str, content_type: str) -> int:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with target.open("wb") as out:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                out.write(chunk)
        return written

    def save_bytes(self, data: bytes, key: str, content_type: str) -> int:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return len(data)

    def open_local(self, key: str) -> Path:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"Object not found: {key}")
        return path

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def signed_url(self, key: str, *, ttl_seconds: int | None = None) -> str:
        signed = sign_storage_key(key, ttl_seconds=ttl_seconds)
        return f"{settings.api_prefix}/files/{quote(key)}?expires={signed['expires']}&signature={signed['signature']}"

    def local_path_for(self, key: str) -> Path:
        return self._path(key)

    def release_local(self, path: Path) -> None:  # nothing to release
        return None


class S3Storage(Storage):
    def __init__(self) -> None:
        import boto3

        if not (settings.s3_bucket and settings.s3_access_key_id):
            raise StorageError("S3 storage selected but credentials are incomplete")
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        self._client = boto3.client(
            "s3",
            region_name=settings.s3_region,
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )
        self._tmp_root = Path(settings.temp_work_path) / "downloads"
        self._tmp_root.mkdir(parents=True, exist_ok=True)

    def _full_key(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def save_stream(self, file_obj, key: str, content_type: str) -> int:
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(0)
        self._client.upload_fileobj(
            file_obj, self.bucket, self._full_key(key), ExtraArgs={"ContentType": content_type}
        )
        return size

    def save_bytes(self, data: bytes, key: str, content_type: str) -> int:
        self._client.put_object(
            Bucket=self.bucket, Key=self._full_key(key), Body=data, ContentType=content_type
        )
        return len(data)

    def open_local(self, key: str) -> Path:
        target = self._tmp_root / f"{uuid.uuid4().hex}-{Path(key).name}"
        self._client.download_file(self.bucket, self._full_key(key), str(target))
        return target

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=self._full_key(key))

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=self._full_key(key))
            return True
        except Exception:  # noqa: BLE001 - boto raises ClientError variants
            return False

    def signed_url(self, key: str, *, ttl_seconds: int | None = None) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self._full_key(key)},
            ExpiresIn=ttl_seconds or settings.signed_url_ttl_seconds,
        )

    def release_local(self, path: Path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


_storage: Storage | None = None


def reset_storage() -> None:
    """Used by tests to rebuild the backend after settings change."""
    global _storage
    _storage = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        if settings.storage_backend == "s3":
            _storage = S3Storage()
        else:
            _storage = LocalStorage(settings.local_storage_path)
    return _storage


def temp_dir(purpose: str) -> Path:
    path = Path(settings.temp_work_path) / purpose / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def disk_free_mb(path: Path | None = None) -> int:
    target = path or Path(settings.temp_work_path)
    target.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(target).free // (1024 * 1024)
