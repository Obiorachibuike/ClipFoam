"""SQLAlchemy models for ClipFoam.

Design notes
------------
* UUID primary keys everywhere (stored as native UUID on PostgreSQL, CHAR(32) on SQLite).
* Every tenant-scoped table carries ``org_id``; queries must always filter it.
* Status columns use portable, non-native enums (VARCHAR + CHECK) so the same
  schema works on PostgreSQL and on the optional SQLite dev configuration.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# --------------------------------------------------------------------------- enums
class MembershipRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class MediaKind(str, enum.Enum):
    VIDEO = "video"
    AUDIO = "audio"


class MediaStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class SourceKind(str, enum.Enum):
    UPLOAD = "upload"
    URL_IMPORT = "url_import"


class TranscriptStatus(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    INGESTING = "ingesting"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    GENERATING_PREVIEWS = "generating_previews"
    RENDERING = "rendering"
    STORING_OUTPUTS = "storing_outputs"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = [
    JobStatus.QUEUED,
    JobStatus.VALIDATING,
    JobStatus.INGESTING,
    JobStatus.TRANSCRIBING,
    JobStatus.ANALYZING,
    JobStatus.GENERATING_PREVIEWS,
    JobStatus.RENDERING,
    JobStatus.STORING_OUTPUTS,
]

TERMINAL_JOB_STATUSES = [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]


class JobType(str, enum.Enum):
    MEDIA_INGEST = "media.ingest"
    MEDIA_IMPORT = "media.import"
    MEDIA_TRANSCRIBE = "media.transcribe"
    PROJECT_GENERATE_CANDIDATES = "project.generate_candidates"
    CLIP_RENDER = "clip.render"
    SOCIAL_PUBLISH = "social.publish"
    PROJECT_DELETE = "project.delete"
    ACCOUNT_DELETE = "account.delete"
    MAINTENANCE_CLEANUP = "maintenance.cleanup"


class CandidateStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CandidateAlgorithm(str, enum.Enum):
    DETERMINISTIC = "deterministic"
    AI_ASSISTED = "ai_assisted"
    MANUAL = "manual"


class ClipStatus(str, enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    RENDERING = "rendering"
    FAILED = "failed"


class AspectRatio(str, enum.Enum):
    PORTRAIT = "9:16"
    SQUARE = "1:1"
    LANDSCAPE = "16:9"
    VERTICAL_4_5 = "4:5"


class ExportKind(str, enum.Enum):
    VIDEO = "video"
    SUBTITLES = "subtitles"
    THUMBNAIL = "thumbnail"


class ExportStatus(str, enum.Enum):
    QUEUED = "queued"
    RENDERING = "rendering"
    READY = "ready"
    FAILED = "failed"


class SocialPlatform(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    X = "x"
    LINKEDIN = "linkedin"
    FACEBOOK = "facebook"
    MASTODON = "mastodon"
    BLUESKY = "bluesky"


class SocialAccountStatus(str, enum.Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class SocialPostStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ScheduledPostStatus(str, enum.Enum):
    PENDING = "pending"
    ENQUEUED = "enqueued"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PublishAttemptStatus(str, enum.Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class UsageKind(str, enum.Enum):
    TRANSCRIPTION_SECONDS = "transcription_seconds"
    RENDER_SECONDS = "render_seconds"
    AI_TOKENS = "ai_tokens"
    UPLOAD_BYTES = "upload_bytes"
    STORAGE_BYTES = "storage_bytes"
    PUBLISH_ATTEMPT = "publish_attempt"


class ContentKind(str, enum.Enum):
    HOOK = "hook"
    TITLE = "title"
    DESCRIPTION = "description"
    CAPTION = "caption"
    HASHTAGS = "hashtags"
    CTA = "cta"
    THUMBNAIL_TEXT = "thumbnail_text"
    PINNED_COMMENT = "pinned_comment"
    SUMMARY = "summary"
    YOUTUBE_TITLE = "youtube_title"
    CHAPTERS = "chapters"
    BLOG = "blog"
    LINKEDIN = "linkedin"
    X_POST = "x_post"
    NEWSLETTER = "newsletter"


class VisualPreset(str, enum.Enum):
    KARAOKE = "karaoke"
    BOLD_CREATOR = "bold_creator"
    MINIMAL = "minimal"
    BOXED = "boxed"
    PODCAST = "podcast"
    NEON = "neon"
    CLASSIC = "classic"
    EDITORIAL = "editorial"


# --------------------------------------------------------------------------- helpers
def _enum(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=48,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- core
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(32), default="free", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationMembership(Base, TimestampMixin):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        Index("ix_membership_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MembershipRole] = mapped_column(
        _enum(MembershipRole, "membership_role"), default=MembershipRole.OWNER, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class BrandKit(Base, TimestampMixin):
    __tablename__ = "brand_kits"
    __table_args__ = (Index("ix_brand_kits_org", "organization_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    primary_color: Mapped[str] = mapped_column(String(9), default="#39FF14", nullable=False)
    secondary_color: Mapped[str] = mapped_column(String(9), default="#101619", nullable=False)
    text_color: Mapped[str] = mapped_column(String(9), default="#F5F7F6", nullable=False)
    highlight_color: Mapped[str] = mapped_column(String(9), default="#39FF14", nullable=False)
    font_family: Mapped[str] = mapped_column(String(80), default="Inter", nullable=False)
    heading_font: Mapped[str] = mapped_column(String(80), default="Inter", nullable=False)
    logo_storage_key: Mapped[str | None] = mapped_column(String(500))
    caption_preset: Mapped[VisualPreset] = mapped_column(
        _enum(VisualPreset, "visual_preset"), default=VisualPreset.BOLD_CREATOR, nullable=False
    )
    caption_style: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    intro_text: Mapped[str | None] = mapped_column(String(200))
    outro_text: Mapped[str | None] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_org_created", "organization_id", "created_at"),
        Index("ix_projects_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    brand_kit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("brand_kits.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProjectStatus] = mapped_column(
        _enum(ProjectStatus, "project_status"), default=ProjectStatus.DRAFT, nullable=False
    )
    source_language: Mapped[str | None] = mapped_column(String(16))
    default_aspect_ratio: Mapped[AspectRatio] = mapped_column(
        _enum(AspectRatio, "aspect_ratio"), default=AspectRatio.PORTRAIT, nullable=False
    )
    default_platform: Mapped[str | None] = mapped_column(String(32))
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    media_assets: Mapped[list[MediaAsset]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    clips: Mapped[list[Clip]] = relationship(back_populates="project", cascade="all, delete-orphan")


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"
    __table_args__ = (
        Index("ix_media_org", "organization_id"),
        Index("ix_media_project", "project_id", "created_at"),
        Index("ix_media_status", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[MediaKind] = mapped_column(
        _enum(MediaKind, "media_kind"), default=MediaKind.VIDEO, nullable=False
    )
    status: Mapped[MediaStatus] = mapped_column(
        _enum(MediaStatus, "media_status"), default=MediaStatus.PENDING, nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    thumbnail_key: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(64))
    audio_codec: Mapped[str | None] = mapped_column(String(64))
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_kind: Mapped[SourceKind] = mapped_column(
        _enum(SourceKind, "source_kind"), default=SourceKind.UPLOAD, nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(2000))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    probe_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="media_assets")
    transcripts: Mapped[list[Transcript]] = relationship(
        back_populates="media_asset", cascade="all, delete-orphan"
    )


class Transcript(Base, TimestampMixin):
    __tablename__ = "transcripts"
    __table_args__ = (
        UniqueConstraint("media_asset_id", name="uq_transcript_media"),
        Index("ix_transcript_org", "organization_id"),
        Index("ix_transcript_cache", "cache_key"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[TranscriptStatus] = mapped_column(
        _enum(TranscriptStatus, "transcript_status"), default=TranscriptStatus.PENDING, nullable=False
    )
    language: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(48), default="faster_whisper", nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_key: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    media_asset: Mapped[MediaAsset] = relationship(back_populates="transcripts")
    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="transcript",
        cascade="all, delete-orphan",
        order_by="TranscriptSegment.index",
    )


class TranscriptSegment(Base, TimestampMixin):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcript_id", "index", name="uq_segment_index"),
        Index("ix_segments_transcript_start", "transcript_id", "start_seconds"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    words: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(64))
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    transcript: Mapped[Transcript] = relationship(back_populates="segments")


class ProcessingJob(Base, TimestampMixin):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
        Index("ix_jobs_org_created", "organization_id", "created_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
        UniqueConstraint("idempotency_key", name="uq_jobs_idempotency"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    media_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE")
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE")
    )
    export_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("exports.id", ondelete="CASCADE")
    )
    social_post_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    type: Mapped[JobType] = mapped_column(_enum(JobType, "job_type"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), default=JobStatus.QUEUED, nullable=False
    )
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_message: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160))


class ClipCandidate(Base, TimestampMixin):
    __tablename__ = "clip_candidates"
    __table_args__ = (
        Index("ix_candidates_project_score", "project_id", "score"),
        Index("ix_candidates_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    transcript_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_title: Mapped[str | None] = mapped_column(String(300))
    hook: Mapped[str | None] = mapped_column(String(400))
    category: Mapped[str | None] = mapped_column(String(60))
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    algorithm: Mapped[CandidateAlgorithm] = mapped_column(
        _enum(CandidateAlgorithm, "candidate_algorithm"),
        default=CandidateAlgorithm.DETERMINISTIC,
        nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[CandidateStatus] = mapped_column(
        _enum(CandidateStatus, "candidate_status"), default=CandidateStatus.PENDING, nullable=False
    )
    validation_status: Mapped[str] = mapped_column(String(32), default="valid", nullable=False)
    validation_notes: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)


class Clip(Base, TimestampMixin):
    __tablename__ = "clips"
    __table_args__ = (
        Index("ix_clips_project_created", "project_id", "created_at"),
        Index("ix_clips_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    media_asset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    brand_kit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("brand_kits.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ClipStatus] = mapped_column(
        _enum(ClipStatus, "clip_status"), default=ClipStatus.DRAFT, nullable=False
    )
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    aspect_ratio: Mapped[AspectRatio] = mapped_column(
        _enum(AspectRatio, "clip_aspect_ratio"), default=AspectRatio.PORTRAIT, nullable=False
    )
    target_platform: Mapped[str | None] = mapped_column(String(32))
    current_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="clips")
    caption_track: Mapped[CaptionTrack | None] = relationship(
        back_populates="clip", cascade="all, delete-orphan", uselist=False
    )
    revisions: Mapped[list[ClipRevision]] = relationship(
        back_populates="clip", cascade="all, delete-orphan", order_by="ClipRevision.revision"
    )
    exports: Mapped[list[Export]] = relationship(back_populates="clip", cascade="all, delete-orphan")


class ClipRevision(Base):
    __tablename__ = "clip_revisions"
    __table_args__ = (UniqueConstraint("clip_id", "revision", name="uq_clip_revision"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    clip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    clip: Mapped[Clip] = relationship(back_populates="revisions")


class CaptionTrack(Base, TimestampMixin):
    __tablename__ = "caption_tracks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    clip_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preset: Mapped[VisualPreset] = mapped_column(
        _enum(VisualPreset, "caption_preset"), default=VisualPreset.BOLD_CREATOR, nullable=False
    )
    style: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # Segment text overrides keyed by transcript segment id (caption-level edits)
    text_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    clip: Mapped[Clip] = relationship(back_populates="caption_track")


class Export(Base, TimestampMixin):
    __tablename__ = "exports"
    __table_args__ = (
        Index("ix_exports_org_created", "organization_id", "created_at"),
        Index("ix_exports_clip", "clip_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[ExportKind] = mapped_column(
        _enum(ExportKind, "export_kind"), default=ExportKind.VIDEO, nullable=False
    )
    status: Mapped[ExportStatus] = mapped_column(
        _enum(ExportStatus, "export_status"), default=ExportStatus.QUEUED, nullable=False
    )
    format: Mapped[str] = mapped_column(String(16), default="mp4", nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    thumbnail_key: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    render_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    clip: Mapped[Clip | None] = relationship(back_populates="exports")


class ContentItem(Base, TimestampMixin):
    __tablename__ = "content_items"
    __table_args__ = (
        Index("ix_content_org_created", "organization_id", "created_at"),
        Index("ix_content_clip", "clip_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE")
    )
    kind: Mapped[ContentKind] = mapped_column(_enum(ContentKind, "content_kind"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(48), default="deterministic", nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)


class SocialAccount(Base, TimestampMixin):
    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "platform", "external_account_id", name="uq_social_account"
        ),
        Index("ix_social_accounts_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        _enum(SocialPlatform, "social_platform"), nullable=False
    )
    external_account_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    access_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[SocialAccountStatus] = mapped_column(
        _enum(SocialAccountStatus, "social_account_status"),
        default=SocialAccountStatus.CONNECTED,
        nullable=False,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    connected_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class SocialPost(Base, TimestampMixin):
    __tablename__ = "social_posts"
    __table_args__ = (
        Index("ix_posts_org_status", "organization_id", "status"),
        Index("ix_posts_project", "project_id", "created_at"),
        UniqueConstraint("idempotency_key", name="uq_posts_idempotency"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE")
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("clips.id", ondelete="SET NULL")
    )
    export_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("exports.id", ondelete="SET NULL")
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("social_accounts.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        _enum(SocialPlatform, "post_platform"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hashtags: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[SocialPostStatus] = mapped_column(
        _enum(SocialPostStatus, "post_status"), default=SocialPostStatus.DRAFT, nullable=False
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    platform_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, default=lambda: str(uuid.uuid4()))
    published_external_id: Mapped[str | None] = mapped_column(String(200))
    published_url: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class ScheduledPost(Base, TimestampMixin):
    __tablename__ = "scheduled_posts"
    __table_args__ = (
        UniqueConstraint("social_post_id", name="uq_scheduled_post"),
        Index("ix_scheduled_due", "status", "scheduled_for"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    social_post_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    status: Mapped[ScheduledPostStatus] = mapped_column(
        _enum(ScheduledPostStatus, "scheduled_status"),
        default=ScheduledPostStatus.PENDING,
        nullable=False,
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    error_message: Mapped[str | None] = mapped_column(Text)


class PublishingAttempt(Base, TimestampMixin):
    __tablename__ = "publishing_attempts"
    __table_args__ = (Index("ix_publish_attempts_post", "social_post_id", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    social_post_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False
    )
    social_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("social_accounts.id", ondelete="SET NULL")
    )
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[PublishAttemptStatus] = mapped_column(
        _enum(PublishAttemptStatus, "publish_attempt_status"),
        default=PublishAttemptStatus.STARTED,
        nullable=False,
    )
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_org_created", "organization_id", "created_at"),
        Index("ix_usage_org_kind", "organization_id", "kind"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    kind: Mapped[UsageKind] = mapped_column(_enum(UsageKind, "usage_kind"), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unit: Mapped[str] = mapped_column(String(24), default="seconds", nullable=False)
    provider: Mapped[str | None] = mapped_column(String(48))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_org_created", "organization_id", "created_at"),
        Index("ix_audit_actor", "actor_user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "ACTIVE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "AspectRatio",
    "AuditLog",
    "BrandKit",
    "CandidateAlgorithm",
    "CandidateStatus",
    "CaptionTrack",
    "Clip",
    "ClipCandidate",
    "ClipRevision",
    "ClipStatus",
    "ContentItem",
    "ContentKind",
    "Export",
    "ExportKind",
    "ExportStatus",
    "JobStatus",
    "JobType",
    "MediaAsset",
    "MediaKind",
    "MediaStatus",
    "MembershipRole",
    "Organization",
    "OrganizationMembership",
    "ProcessingJob",
    "Project",
    "ProjectStatus",
    "PublishAttemptStatus",
    "PublishingAttempt",
    "ScheduledPost",
    "ScheduledPostStatus",
    "SocialAccount",
    "SocialAccountStatus",
    "SocialPlatform",
    "SocialPost",
    "SocialPostStatus",
    "SourceKind",
    "Transcript",
    "TranscriptSegment",
    "TranscriptStatus",
    "UsageKind",
    "UsageRecord",
    "User",
    "VisualPreset",
]
