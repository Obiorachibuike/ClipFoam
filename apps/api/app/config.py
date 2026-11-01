"""Application configuration.

Every setting is driven by environment variables (prefix ``CF_``) so the same
image can run as API, worker, or a one-shot CLI process. Secrets never have
usable defaults: the app refuses to boot in production with placeholder values.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent


def _csv(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return value
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CF_",
        env_file=(REPO_ROOT / ".env", API_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ core
    app_name: str = "ClipFoam API"
    app_env: str = "development"  # development | test | production
    api_prefix: str = "/api/v1"
    secret_key: str = "dev-only-insecure-secret-change-me"
    access_token_ttl_minutes: int = 60 * 24 * 7
    enable_signup: bool = True
    public_app_url: str = "http://localhost:3000"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # --------------------------------------------------------------- database
    database_url: str = f"sqlite:///{API_DIR / 'data' / 'clipfoam.db'}"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # ---------------------------------------------------------------- security
    cookie_name: str = "clipfoam_session"
    cookie_csrf_name: str = "clipfoam_csrf"
    cookie_secure: bool = False
    cookie_domain: str | None = None
    csrf_enabled: bool = True
    rate_limit_per_minute: int = 240
    login_rate_limit_per_minute: int = 20
    redis_url: str | None = None
    token_encryption_key: str | None = None

    # ---------------------------------------------------------------- storage
    storage_backend: str = "local"  # local | s3
    local_storage_path: Path = API_DIR / "data" / "media"
    temp_work_path: Path = API_DIR / "data" / "work"
    signed_url_ttl_seconds: int = 900
    max_upload_mb: int = 512
    storage_quota_mb: int = 20_000
    s3_bucket: str = ""
    s3_region: str = "auto"
    s3_endpoint_url: str | None = None
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_prefix: str = "clipfoam"

    # ------------------------------------------------------------ media limits
    max_duration_seconds: int = 3 * 60 * 60
    min_duration_seconds: int = 1
    allowed_video_ext: str = "mp4,mov,mkv,webm,m4v,avi"
    allowed_audio_ext: str = "mp3,wav,m4a,aac,flac,ogg,opus"
    ffmpeg_bin: str | None = None
    ffprobe_bin: str | None = None
    render_timeout_seconds: int = 900
    render_concurrency: int = 1
    min_free_disk_mb: int = 512

    # ---------------------------------------------------------- transcription
    transcription_provider: str = "faster_whisper"  # faster_whisper | manual
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    whisper_batch_size: int = 8
    whisper_beam_size: int = 5
    whisper_word_timestamps: bool = True
    whisper_download_root: str | None = None
    transcription_cache: bool = True

    # ------------------------------------------------------------------- AI
    ai_provider: str = "none"  # none | ollama | openai_compatible
    ai_model: str | None = None
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_timeout_seconds: int = 90
    ai_max_retries: int = 2
    ai_temperature: float = 0.3
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # ------------------------------------------------------------- highlights
    default_clip_count: int = 6
    min_clip_seconds: float = 18.0
    max_clip_seconds: float = 90.0
    candidate_pause_threshold: float = 0.6

    # --------------------------------------------------------------- workers
    worker_concurrency: int = 2
    worker_poll_interval: float = 1.0
    job_lease_seconds: int = 120
    job_max_attempts: int = 3
    job_backoff_seconds: int = 15
    enable_inline_worker: bool = False  # dev convenience: run jobs in the API

    # ------------------------------------------------------------- retention
    retention_days: int = 30
    temp_cleanup_hours: int = 6
    usage_free_transcription_minutes: int = 120
    usage_free_render_minutes: int = 60

    # ---------------------------------------------------------------- imports
    allow_url_import: bool = True
    allow_private_import_networks: bool = False
    import_max_bytes: int = 512 * 1024 * 1024
    import_timeout_seconds: int = 600
    import_allowed_hosts: str = ""  # optional allowlist; empty = any public host

    # ---------------------------------------------------------------- social
    oauth_redirect_base_url: str = "http://localhost:8000"
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    mastodon_client_id: str = ""
    mastodon_client_secret: str = ""
    mastodon_instance: str = "https://mastodon.social"
    social_publish_enabled: bool = True

    # --------------------------------------------------------------- derived
    @field_validator("transcription_provider")
    @classmethod
    def _valid_transcription(cls, v: str) -> str:
        allowed = {"faster_whisper", "manual"}
        if v not in allowed:
            raise ValueError(f"transcription_provider must be one of {sorted(allowed)}")
        return v

    @field_validator("storage_backend")
    @classmethod
    def _valid_storage(cls, v: str) -> str:
        allowed = {"local", "s3"}
        if v not in allowed:
            raise ValueError(f"storage_backend must be one of {sorted(allowed)}")
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return _csv(self.cors_origins)

    @property
    def video_extensions(self) -> set[str]:
        return set(_csv(self.allowed_video_ext))

    @property
    def audio_extensions(self) -> set[str]:
        return set(_csv(self.allowed_audio_ext))

    @property
    def import_host_allowlist(self) -> list[str]:
        return _csv(self.import_allowed_hosts)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    def ffmpeg_path(self) -> str:
        """Resolve a usable ffmpeg binary (config → PATH → imageio bundle)."""
        if self.ffmpeg_bin:
            return self.ffmpeg_bin
        found = shutil.which("ffmpeg")
        if found:
            return found
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # pragma: no cover - only when bundle missing
            return "ffmpeg"

    def ffprobe_path(self) -> str:
        if self.ffprobe_bin:
            return self.ffprobe_bin
        return shutil.which("ffprobe") or "ffprobe"

    def has_ffprobe(self) -> bool:
        probe = self.ffprobe_path()
        return shutil.which(probe) is not None or Path(probe).exists()

    def validate_runtime(self) -> list[str]:
        """Configuration problems that must be surfaced to operators."""
        problems: list[str] = []
        if self.is_production:
            if self.secret_key.startswith("dev-only") or len(self.secret_key) < 32:
                problems.append("CF_SECRET_KEY must be a long random value in production")
            if self.cookie_secure is False:
                problems.append("CF_COOKIE_SECURE must be true in production")
            if "*" in self.cors_origin_list:
                problems.append("CF_CORS_ORIGINS must not contain '*' in production")
            if self.storage_backend == "local":
                problems.append(
                    "CF_STORAGE_BACKEND=local uses container disk; configure S3/R2 for production"
                )
        if self.storage_backend == "s3":
            if not (self.s3_bucket and self.s3_access_key_id and self.s3_secret_access_key):
                problems.append("S3 storage selected but bucket/credentials are incomplete")
        if self.ai_provider == "openai_compatible" and not self.ai_api_key:
            problems.append("CF_AI_PROVIDER=openai_compatible requires CF_AI_API_KEY")
        if self.ai_provider not in {"none", "ollama", "openai_compatible"}:
            problems.append(f"Unknown CF_AI_PROVIDER={self.ai_provider}")
        if not self.has_ffprobe():
            problems.append(
                "ffprobe not found on PATH - metadata extraction falls back to PyAV "
                "(install ffmpeg for full fidelity)"
            )
        return problems

    def ai_configured(self) -> bool:
        if self.ai_provider == "ollama":
            return bool(self.ollama_base_url and self.ollama_model)
        if self.ai_provider == "openai_compatible":
            return bool(self.ai_api_key and (self.ai_base_url or self.ai_model))
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
