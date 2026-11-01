"""Pydantic request/response models (validated at the API boundary)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import (
    AspectRatio,
    CandidateAlgorithm,
    CandidateStatus,
    ClipStatus,
    ContentKind,
    ExportKind,
    ExportStatus,
    JobStatus,
    JobType,
    MediaKind,
    MediaStatus,
    MembershipRole,
    ProjectStatus,
    PublishAttemptStatus,
    ScheduledPostStatus,
    SocialAccountStatus,
    SocialPlatform,
    SocialPostStatus,
    UsageKind,
    VisualPreset,
)

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- auth
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)
    organization_name: str | None = Field(default=None, max_length=160)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, value: str) -> str:
        if value.isdigit() or value.isalpha():
            raise ValueError("Password must mix letters and numbers or symbols")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    avatar_url: str | None
    is_superadmin: bool
    created_at: datetime
    last_login_at: datetime | None


class OrganizationOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    plan: str
    created_at: datetime


class MembershipOut(ORMModel):
    organization_id: uuid.UUID
    role: MembershipRole
    organization: OrganizationOut


class SessionOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    memberships: list[MembershipOut]
    role: MembershipRole
    csrf_token: str
    limits: dict[str, Any]


class UpdateProfileRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    avatar_url: str | None = Field(default=None, max_length=500)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class UpdateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)


# --------------------------------------------------------------------------- projects
class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    default_aspect_ratio: AspectRatio = AspectRatio.PORTRAIT
    default_platform: str | None = Field(default=None, max_length=32)
    source_language: str | None = Field(default=None, max_length=16)
    brand_kit_id: uuid.UUID | None = None


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    status: ProjectStatus | None = None
    default_aspect_ratio: AspectRatio | None = None
    default_platform: str | None = Field(default=None, max_length=32)
    source_language: str | None = Field(default=None, max_length=16)
    brand_kit_id: uuid.UUID | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    title: str
    description: str | None
    status: ProjectStatus
    default_aspect_ratio: AspectRatio
    default_platform: str | None
    source_language: str | None
    brand_kit_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectOut):
    media_count: int = 0
    clip_count: int = 0
    candidate_count: int = 0
    export_count: int = 0
    primary_media: "MediaAssetOut | None" = None
    transcript_status: str | None = None
    active_jobs: list["JobOut"] = []


# --------------------------------------------------------------------------- media
class MediaAssetOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    kind: MediaKind
    status: MediaStatus
    original_filename: str
    mime_type: str
    extension: str
    size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    has_audio: bool
    source_kind: str
    thumbnail_url: str | None = None
    stream_url: str | None = None
    error_message: str | None
    created_at: datetime


class UploadResponse(BaseModel):
    media: MediaAssetOut
    job: "JobOut"


class UrlImportRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2000)

    @field_validator("url")
    @classmethod
    def _https_only(cls, value: str) -> str:
        if not value.lower().startswith(("https://", "http://")):
            raise ValueError("URL must use http(s)")
        return value


class TranscriptSegmentOut(ORMModel):
    id: uuid.UUID
    index: int
    start_seconds: float
    end_seconds: float
    text: str
    original_text: str
    words: list[Any]
    speaker: str | None
    is_edited: bool


class TranscriptOut(ORMModel):
    id: uuid.UUID
    media_asset_id: uuid.UUID
    status: str
    language: str | None
    provider: str
    model: str | None
    is_manual: bool
    duration_seconds: float | None
    word_count: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    segments: list[TranscriptSegmentOut] = []


class TranscriptSegmentUpdate(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    speaker: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_window(self) -> "TranscriptSegmentUpdate":
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds <= self.start_seconds
        ):
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class TranscriptImportRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)
    format: Literal["srt", "vtt", "json", "plain"] = "srt"
    language: str | None = Field(default=None, max_length=16)

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        return value


class TranscribeRequest(BaseModel):
    language: str | None = Field(default=None, max_length=16)
    force: bool = False


# --------------------------------------------------------------------------- candidates
class CandidateGenerationRequest(BaseModel):
    clip_count: int = Field(default=6, ge=1, le=30)
    min_duration: float = Field(default=18, ge=5, le=600)
    max_duration: float = Field(default=90, ge=10, le=900)
    aspect_ratio: AspectRatio | None = None
    target_platform: str | None = Field(default=None, max_length=32)
    use_ai: bool = False
    language: str | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def _duration_order(self) -> "CandidateGenerationRequest":
        if self.max_duration <= self.min_duration:
            raise ValueError("max_duration must exceed min_duration")
        return self


class CandidateOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    transcript_id: uuid.UUID
    rank: int
    start_seconds: float
    end_seconds: float
    excerpt: str
    suggested_title: str | None
    hook: str | None
    category: str | None
    score: float
    score_breakdown: dict[str, Any]
    explanation: str | None
    algorithm: CandidateAlgorithm
    model: str | None
    status: CandidateStatus
    validation_status: str
    validation_notes: str | None
    metadata_json: dict[str, Any]
    created_at: datetime


class CandidateUpdate(BaseModel):
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    suggested_title: str | None = Field(default=None, max_length=300)
    hook: str | None = Field(default=None, max_length=400)
    excerpt: str | None = Field(default=None, max_length=8000)
    category: str | None = Field(default=None, max_length=60)
    status: CandidateStatus | None = None


class CandidateGenerateResponse(BaseModel):
    job: "JobOut"


# --------------------------------------------------------------------------- clips & captions
class CandidateApproveRequest(BaseModel):
    """Overrides applied when a candidate is approved into a clip."""

    title: str | None = Field(default=None, max_length=300)
    aspect_ratio: AspectRatio | None = None
    target_platform: str | None = Field(default=None, max_length=32)
    brand_kit_id: uuid.UUID | None = None


class ClipCreate(BaseModel):
    candidate_id: uuid.UUID | None = None
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=300)
    aspect_ratio: AspectRatio | None = None
    target_platform: str | None = Field(default=None, max_length=32)
    brand_kit_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _needs_bounds(self) -> "ClipCreate":
        if self.candidate_id is None and (self.start_seconds is None or self.end_seconds is None):
            raise ValueError("Provide candidate_id or both start_seconds and end_seconds")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds <= self.start_seconds
        ):
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class ClipSettings(BaseModel):
    """Render-affecting clip settings. Stored on the clip and snapshotted per export."""

    resolution: Literal["720p", "1080p", "1440p", "2160p"] = "1080p"
    quality: Literal["draft", "standard", "high"] = "standard"
    remove_silence: bool = False
    silence_min_seconds: float = Field(default=0.6, ge=0.2, le=3.0)
    remove_filler_words: bool = False
    normalize_audio: bool = True
    background_music: Literal["none", "ambient_soft", "upbeat", "cinematic"] = "none"
    background_music_gain_db: float = Field(default=-18.0, ge=-40.0, le=0.0)
    reframe_mode: Literal["center", "face", "motion"] = "center"
    face_tracking: bool = False
    intro_text: str | None = Field(default=None, max_length=120)
    outro_text: str | None = Field(default=None, max_length=120)
    overlay_text: str | None = Field(default=None, max_length=200)
    overlay_position: Literal["top", "bottom"] = "top"
    logo_enabled: bool = False
    captions_enabled: bool = True
    safe_area_margin: int = Field(default=8, ge=0, le=20)
    fps: int = Field(default=30, ge=15, le=60)


class ClipUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None, ge=0)
    aspect_ratio: AspectRatio | None = None
    target_platform: str | None = Field(default=None, max_length=32)
    settings: ClipSettings | None = None
    brand_kit_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _window(self) -> "ClipUpdate":
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds <= self.start_seconds
        ):
            raise ValueError("end_seconds must be greater than start_seconds")
        return self


class ClipOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    media_asset_id: uuid.UUID
    candidate_id: uuid.UUID | None
    title: str
    description: str | None
    status: ClipStatus
    start_seconds: float
    end_seconds: float
    aspect_ratio: AspectRatio
    target_platform: str | None
    settings: dict[str, Any]
    current_revision: int
    brand_kit_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ClipRevisionOut(ORMModel):
    id: uuid.UUID
    revision: int
    title: str
    start_seconds: float
    end_seconds: float
    settings: dict[str, Any]
    note: str | None
    created_at: datetime


class CaptionStyle(BaseModel):
    preset: VisualPreset = VisualPreset.BOLD_CREATOR
    font_family: str = Field(default="Inter", max_length=80)
    font_size: int = Field(default=58, ge=18, le=140)
    text_color: str = Field(default="#F5F7F6", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    highlight_color: str = Field(default="#39FF14", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    background_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    background_opacity: float = Field(default=0.0, ge=0.0, le=1.0)
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")
    outline_width: float = Field(default=3.0, ge=0, le=12)
    shadow: float = Field(default=1.0, ge=0, le=8)
    position: Literal["top", "center", "bottom"] = "bottom"
    alignment: Literal["left", "center", "right"] = "center"
    max_chars_per_line: int = Field(default=22, ge=8, le=64)
    max_lines: int = Field(default=2, ge=1, le=4)
    uppercase: bool = False
    animation: Literal["none", "karaoke", "pop", "fade", "slide"] = "karaoke"
    safe_area_margin: int = Field(default=8, ge=0, le=20)
    words_per_caption: int = Field(default=4, ge=1, le=12)


class CaptionTrackOut(ORMModel):
    id: uuid.UUID
    clip_id: uuid.UUID
    enabled: bool
    preset: VisualPreset
    style: dict[str, Any]
    text_overrides: dict[str, Any]
    version: int
    updated_at: datetime


class CaptionTrackUpdate(BaseModel):
    enabled: bool | None = None
    style: CaptionStyle | None = None
    text_overrides: dict[str, str] | None = None


class CaptionPreviewLine(BaseModel):
    start: float
    end: float
    words: list[dict[str, Any]]
    text: str


class CaptionPreview(BaseModel):
    lines: list[CaptionPreviewLine]
    style: dict[str, Any]
    warnings: list[str] = []


# --------------------------------------------------------------------------- rendering & exports
class RenderRequest(BaseModel):
    settings: ClipSettings | None = None
    force: bool = False


class ExportOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    clip_id: uuid.UUID | None
    kind: ExportKind
    status: ExportStatus
    format: str
    size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    render_settings: dict[str, Any]
    error_message: str | None
    download_count: int
    created_at: datetime
    completed_at: datetime | None


class DownloadLink(BaseModel):
    url: str
    expires_at: int
    filename: str


# --------------------------------------------------------------------------- content
class ContentGenerateRequest(BaseModel):
    kinds: list[ContentKind] = Field(min_length=1, max_length=12)
    platform: str | None = Field(default=None, max_length=32)
    tone: Literal["punchy", "professional", "casual", "educational", "bold"] = "punchy"
    provider: str | None = Field(default=None, max_length=32)
    count: int = Field(default=3, ge=1, le=6)


class ContentItemOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    clip_id: uuid.UUID | None
    kind: ContentKind
    title: str | None
    body: str
    provider: str
    model: str | None
    is_edited: bool
    version: int
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ContentItemUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=40_000)


# --------------------------------------------------------------------------- brand kits
class BrandKitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    primary_color: str = Field(default="#39FF14", pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str = Field(default="#101619", pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str = Field(default="#F5F7F6", pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_color: str = Field(default="#39FF14", pattern=r"^#[0-9A-Fa-f]{6}$")
    font_family: str = Field(default="Inter", max_length=80)
    heading_font: str = Field(default="Inter", max_length=80)
    caption_preset: VisualPreset = VisualPreset.BOLD_CREATOR
    caption_style: dict[str, Any] = {}
    intro_text: str | None = Field(default=None, max_length=200)
    outro_text: str | None = Field(default=None, max_length=200)
    is_default: bool = False


class BrandKitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    primary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    text_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    highlight_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    font_family: str | None = Field(default=None, max_length=80)
    heading_font: str | None = Field(default=None, max_length=80)
    caption_preset: VisualPreset | None = None
    caption_style: dict[str, Any] | None = None
    intro_text: str | None = Field(default=None, max_length=200)
    outro_text: str | None = Field(default=None, max_length=200)
    is_default: bool | None = None


class BrandKitOut(ORMModel):
    id: uuid.UUID
    name: str
    primary_color: str
    secondary_color: str
    text_color: str
    highlight_color: str
    font_family: str
    heading_font: str
    caption_preset: VisualPreset
    caption_style: dict[str, Any]
    intro_text: str | None
    outro_text: str | None
    is_default: bool
    logo_url: str | None = None
    created_at: datetime
    updated_at: datetime


class LogoUploadResponse(BaseModel):
    logo_url: str | None


# --------------------------------------------------------------------------- social
class SocialProviderInfo(BaseModel):
    id: str
    name: str
    status: Literal["available", "needs_configuration", "unavailable", "manual_export"]
    supports_oauth: bool
    supported_media: list[str]
    supported_formats: list[str]
    required_permissions: list[str]
    upload_restrictions: list[str]
    publishing_limitations: list[str]
    supports_status_check: bool
    notes: str | None = None


class SocialAccountOut(ORMModel):
    id: uuid.UUID
    platform: SocialPlatform
    external_account_id: str
    display_name: str
    avatar_url: str | None
    status: SocialAccountStatus
    scopes: list[Any]
    token_expires_at: datetime | None
    metadata_json: dict[str, Any]
    created_at: datetime


class ConnectStartResponse(BaseModel):
    authorization_url: str
    state: str


class SocialPostCreate(BaseModel):
    platform: SocialPlatform
    title: str | None = Field(default=None, max_length=300)
    body: str = Field(default="", max_length=20000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    project_id: uuid.UUID | None = None
    clip_id: uuid.UUID | None = None
    export_id: uuid.UUID | None = None
    social_account_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    timezone: str = Field(default="UTC", max_length=64)
    platform_metadata: dict[str, Any] = {}


class SocialPostUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=20000)
    hashtags: list[str] | None = Field(default=None, max_length=30)
    scheduled_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    platform_metadata: dict[str, Any] | None = None
    social_account_id: uuid.UUID | None = None


class PublishRequest(BaseModel):
    social_account_id: uuid.UUID | None = None


class PublishingAttemptOut(ORMModel):
    id: uuid.UUID
    attempt: int
    status: PublishAttemptStatus
    error_code: str | None
    error_message: str | None
    response_payload: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None


class SocialPostOut(ORMModel):
    id: uuid.UUID
    platform: SocialPlatform
    project_id: uuid.UUID | None
    clip_id: uuid.UUID | None
    export_id: uuid.UUID | None
    social_account_id: uuid.UUID | None
    title: str | None
    body: str
    hashtags: list[Any]
    status: SocialPostStatus
    scheduled_at: datetime | None
    timezone: str
    platform_metadata: dict[str, Any]
    published_external_id: str | None
    published_url: str | None
    published_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    attempts: list[PublishingAttemptOut] = []


class ScheduledPostOut(ORMModel):
    id: uuid.UUID
    social_post_id: uuid.UUID
    scheduled_for: datetime
    timezone: str
    status: ScheduledPostStatus
    error_message: str | None


# --------------------------------------------------------------------------- jobs
class JobOut(ORMModel):
    id: uuid.UUID
    type: JobType
    status: JobStatus
    progress: int
    stage_message: str | None
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any]
    error_code: str | None
    error_message: str | None
    cancellation_requested: bool
    project_id: uuid.UUID | None
    clip_id: uuid.UUID | None
    export_id: uuid.UUID | None
    media_asset_id: uuid.UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobEvent(BaseModel):
    job_id: uuid.UUID
    status: JobStatus
    progress: int
    stage_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] = {}
    updated_at: datetime | None = None


# --------------------------------------------------------------------------- analytics & settings
class UsageBreakdown(BaseModel):
    kind: UsageKind
    quantity: float
    unit: str


class StorageSummary(BaseModel):
    total_bytes: int
    quota_bytes: int
    media_count: int
    export_count: int
    used_percent: float


class DashboardStats(BaseModel):
    projects: int
    clips: int
    exports: int
    published_posts: int
    scheduled_posts: int
    failed_jobs: int
    transcription_minutes: float
    render_minutes: float
    storage: StorageSummary
    recent_jobs: list[JobOut]


class PlatformMetric(BaseModel):
    platform: str
    status: Literal["available", "not_connected", "permission_missing", "unsupported"]
    detail: str | None = None
    metrics: dict[str, float] = {}


class AnalyticsOverview(BaseModel):
    usage: list[UsageBreakdown]
    platforms: list[PlatformMetric]
    publishes_total: int
    publish_failures: int
    posts_by_status: dict[str, int]


class ProviderStatus(BaseModel):
    id: str
    label: str
    configured: bool
    detail: str | None = None


class RuntimeConfigOut(BaseModel):
    app_env: str
    max_upload_mb: int
    max_duration_seconds: int
    allowed_video_extensions: list[str]
    allowed_audio_extensions: list[str]
    transcription: ProviderStatus
    ai: ProviderStatus
    storage: ProviderStatus
    ffmpeg_available: bool
    ffprobe_available: bool
    enable_signup: bool
    social_publish_enabled: bool
    retention_days: int
    usage_limits: dict[str, Any]


class AuditLogOut(ORMModel):
    id: uuid.UUID
    action: str
    target_type: str | None
    target_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime


ProjectDetail.model_rebuild()
UploadResponse.model_rebuild()
CandidateGenerateResponse.model_rebuild()
