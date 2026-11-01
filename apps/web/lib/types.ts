// Shared API types (mirrors the FastAPI schemas).

export type Role = 'owner' | 'admin' | 'editor' | 'viewer';

export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  is_superadmin: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  plan: string;
  created_at: string;
}

export interface Membership {
  organization_id: string;
  role: Role;
  organization: Organization;
}

export interface Session {
  user: User;
  organization: Organization;
  memberships: Membership[];
  role: Role;
  csrf_token: string;
  limits: Record<string, unknown>;
}

export type ProjectStatus = 'draft' | 'active' | 'archived';
export type AspectRatio = '9:16' | '4:5' | '1:1' | '16:9';

export interface Project {
  id: string;
  title: string;
  description: string | null;
  status: ProjectStatus;
  default_aspect_ratio: AspectRatio;
  default_platform: string | null;
  source_language: string | null;
  brand_kit_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail extends Project {
  media_count: number;
  clip_count: number;
  candidate_count: number;
  export_count: number;
  primary_media: MediaAsset | null;
  transcript_status: string | null;
  active_jobs: Job[];
}

export type MediaStatus = 'pending' | 'processing' | 'ready' | 'failed' | 'deleted';

export interface MediaAsset {
  id: string;
  project_id: string;
  kind: 'video' | 'audio';
  status: MediaStatus;
  original_filename: string;
  mime_type: string;
  extension: string;
  size_bytes: number;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  has_audio: boolean;
  source_kind: string;
  thumbnail_url: string | null;
  stream_url: string | null;
  error_message: string | null;
  created_at: string;
}

export interface UploadResponse {
  media: MediaAsset;
  job: Job;
}

export interface TranscriptWord {
  word: string;
  start: number;
  end: number;
  probability?: number;
}

export interface TranscriptSegment {
  id: string;
  index: number;
  start_seconds: number;
  end_seconds: number;
  text: string;
  original_text: string;
  words: TranscriptWord[];
  speaker: string | null;
  is_edited: boolean;
}

export interface Transcript {
  id: string;
  media_asset_id: string;
  status: 'pending' | 'ready' | 'failed';
  language: string | null;
  provider: string;
  model: string | null;
  is_manual: boolean;
  duration_seconds: number | null;
  word_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  segments: TranscriptSegment[];
}

export type JobStatus =
  | 'queued'
  | 'validating'
  | 'ingesting'
  | 'transcribing'
  | 'analyzing'
  | 'generating_previews'
  | 'rendering'
  | 'storing_outputs'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type JobType =
  | 'media.ingest'
  | 'media.import'
  | 'media.transcribe'
  | 'project.generate_candidates'
  | 'clip.render'
  | 'social.publish'
  | 'project.delete'
  | 'account.delete'
  | 'maintenance.cleanup';

export interface Job {
  id: string;
  type: JobType;
  status: JobStatus;
  progress: number;
  stage_message: string | null;
  attempts: number;
  max_attempts: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  cancellation_requested: boolean;
  project_id: string | null;
  clip_id: string | null;
  export_id: string | null;
  media_asset_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export type CandidateStatus = 'pending' | 'approved' | 'rejected' | 'superseded';

export interface ClipCandidate {
  id: string;
  project_id: string;
  transcript_id: string;
  rank: number;
  start_seconds: number;
  end_seconds: number;
  excerpt: string;
  suggested_title: string | null;
  hook: string | null;
  category: string | null;
  score: number;
  score_breakdown: Record<string, number>;
  explanation: string | null;
  algorithm: 'deterministic' | 'ai_assisted' | 'manual';
  model: string | null;
  status: CandidateStatus;
  validation_status: string;
  validation_notes: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export type ClipStatus = 'draft' | 'ready' | 'rendering' | 'failed';

export interface ClipSettings {
  resolution: '720p' | '1080p' | '1440p' | '2160p';
  quality: 'draft' | 'standard' | 'high';
  remove_silence: boolean;
  silence_min_seconds: number;
  remove_filler_words: boolean;
  normalize_audio: boolean;
  background_music: 'none' | 'ambient_soft' | 'upbeat' | 'cinematic';
  background_music_gain_db: number;
  reframe_mode: 'center' | 'face' | 'motion';
  face_tracking: boolean;
  intro_text: string | null;
  outro_text: string | null;
  overlay_text: string | null;
  overlay_position: 'top' | 'bottom';
  logo_enabled: boolean;
  captions_enabled: boolean;
  safe_area_margin: number;
  fps: number;
}

export interface Clip {
  id: string;
  project_id: string;
  media_asset_id: string;
  candidate_id: string | null;
  title: string;
  description: string | null;
  status: ClipStatus;
  start_seconds: number;
  end_seconds: number;
  aspect_ratio: AspectRatio;
  target_platform: string | null;
  settings: Partial<ClipSettings>;
  current_revision: number;
  brand_kit_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ClipRevision {
  id: string;
  revision: number;
  title: string;
  start_seconds: number;
  end_seconds: number;
  settings: Record<string, unknown>;
  note: string | null;
  created_at: string;
}

export type VisualPreset =
  | 'karaoke'
  | 'bold_creator'
  | 'minimal'
  | 'boxed'
  | 'podcast'
  | 'neon'
  | 'classic'
  | 'editorial';

export interface CaptionStyle {
  preset: VisualPreset;
  font_family: string;
  font_size: number;
  text_color: string;
  highlight_color: string;
  background_color: string;
  background_opacity: number;
  outline_color: string;
  outline_width: number;
  shadow: number;
  position: 'top' | 'center' | 'bottom';
  alignment: 'left' | 'center' | 'right';
  max_chars_per_line: number;
  max_lines: number;
  uppercase: boolean;
  animation: 'none' | 'karaoke' | 'pop' | 'fade' | 'slide';
  safe_area_margin: number;
  words_per_caption: number;
}

export interface CaptionTrack {
  id: string;
  clip_id: string;
  enabled: boolean;
  preset: VisualPreset;
  style: Partial<CaptionStyle>;
  text_overrides: Record<string, string>;
  version: number;
  updated_at: string;
}

export interface CaptionLine {
  start: number;
  end: number;
  text: string;
  words: { text: string; start: number; end: number }[];
}

export interface CaptionPreview {
  lines: CaptionLine[];
  style: CaptionStyle;
  warnings: string[];
}

export type ExportStatus = 'queued' | 'rendering' | 'ready' | 'failed';

export interface Export {
  id: string;
  project_id: string;
  clip_id: string | null;
  kind: 'video' | 'subtitles' | 'thumbnail';
  status: ExportStatus;
  format: string;
  size_bytes: number;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  render_settings: Record<string, unknown>;
  error_message: string | null;
  download_count: number;
  created_at: string;
  completed_at: string | null;
}

export interface DownloadLink {
  url: string;
  expires_at: number;
  filename: string;
}

export type ContentKind =
  | 'hook'
  | 'title'
  | 'description'
  | 'caption'
  | 'hashtags'
  | 'cta'
  | 'thumbnail_text'
  | 'pinned_comment'
  | 'summary'
  | 'youtube_title'
  | 'chapters'
  | 'blog'
  | 'linkedin'
  | 'x_post'
  | 'newsletter';

export interface ContentItem {
  id: string;
  project_id: string;
  clip_id: string | null;
  kind: ContentKind;
  title: string | null;
  body: string;
  provider: string;
  model: string | null;
  is_edited: boolean;
  version: number;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface BrandKit {
  id: string;
  name: string;
  primary_color: string;
  secondary_color: string;
  text_color: string;
  highlight_color: string;
  font_family: string;
  heading_font: string;
  caption_preset: VisualPreset;
  caption_style: Record<string, unknown>;
  intro_text: string | null;
  outro_text: string | null;
  is_default: boolean;
  logo_url: string | null;
  created_at: string;
  updated_at: string;
}

export type SocialPlatform =
  | 'youtube'
  | 'tiktok'
  | 'instagram'
  | 'x'
  | 'linkedin'
  | 'facebook'
  | 'mastodon'
  | 'bluesky';

export interface SocialProvider {
  id: string;
  name: string;
  status: 'available' | 'needs_configuration' | 'unavailable' | 'manual_export';
  supports_oauth: boolean;
  supported_media: string[];
  supported_formats: string[];
  required_permissions: string[];
  upload_restrictions: string[];
  publishing_limitations: string[];
  supports_status_check: boolean;
  notes: string | null;
}

export interface SocialAccount {
  id: string;
  platform: SocialPlatform;
  external_account_id: string;
  display_name: string;
  avatar_url: string | null;
  status: 'connected' | 'expired' | 'revoked' | 'error';
  scopes: string[];
  token_expires_at: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export type SocialPostStatus = 'draft' | 'scheduled' | 'publishing' | 'published' | 'failed' | 'cancelled';

export interface PublishingAttempt {
  id: string;
  attempt: number;
  status: 'started' | 'succeeded' | 'failed' | 'skipped';
  error_code: string | null;
  error_message: string | null;
  response_payload: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
}

export interface SocialPost {
  id: string;
  platform: SocialPlatform;
  project_id: string | null;
  clip_id: string | null;
  export_id: string | null;
  social_account_id: string | null;
  title: string | null;
  body: string;
  hashtags: string[];
  status: SocialPostStatus;
  scheduled_at: string | null;
  timezone: string;
  platform_metadata: Record<string, unknown>;
  published_external_id: string | null;
  published_url: string | null;
  published_at: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  attempts: PublishingAttempt[];
}

export interface DashboardStats {
  projects: number;
  clips: number;
  exports: number;
  published_posts: number;
  scheduled_posts: number;
  failed_jobs: number;
  transcription_minutes: number;
  render_minutes: number;
  storage: {
    total_bytes: number;
    quota_bytes: number;
    media_count: number;
    export_count: number;
    used_percent: number;
  };
  recent_jobs: Job[];
}

export interface AnalyticsOverview {
  usage: { kind: string; quantity: number; unit: string }[];
  platforms: {
    platform: string;
    status: string;
    detail: string | null;
    metrics: Record<string, number>;
  }[];
  publishes_total: number;
  publish_failures: number;
  posts_by_status: Record<string, number>;
}

export interface RuntimeConfig {
  app_env: string;
  max_upload_mb: number;
  max_duration_seconds: number;
  allowed_video_extensions: string[];
  allowed_audio_extensions: string[];
  transcription: { id: string; label: string; configured: boolean; detail: string | null };
  ai: { id: string; label: string; configured: boolean; detail: string | null };
  storage: { id: string; label: string; configured: boolean; detail: string | null };
  ffmpeg_available: boolean;
  ffprobe_available: boolean;
  enable_signup: boolean;
  social_publish_enabled: boolean;
  retention_days: number;
  usage_limits: Record<string, number>;
}

export interface AuditLogEntry {
  id: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type ApiErrorPayload = { code: string; message: string; details?: unknown };
