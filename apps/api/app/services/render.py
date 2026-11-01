"""Clip rendering with FFmpeg.

Every requested operation is validated before a process is spawned, commands are
built as argument arrays, and the effective render configuration is returned so
it can be persisted on the export record.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.config import settings
from app.services.captions import (
    CaptionError,
    CaptionLine,
    assign_ass_color,
    build_ass,
    canvas_for_ratio,
    format_ass_timestamp,
    estimate_reading_speed,
)
from app.services.media import MediaError, MediaInfo, probe_media, run_command
from app.storage import disk_free_mb

logger = logging.getLogger(__name__)

QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    "draft": {"crf": 28, "preset": "veryfast", "audio_bitrate": "128k"},
    "standard": {"crf": 23, "preset": "medium", "audio_bitrate": "160k"},
    "high": {"crf": 18, "preset": "slow", "audio_bitrate": "192k"},
}

RESOLUTION_HEIGHTS = {"720p": 1280, "1080p": 1920, "1440p": 2560, "2160p": 3840}

MUSIC_CHOICES = {"none", "ambient_soft", "upbeat", "cinematic"}


class RenderValidationError(ValueError):
    """A render request that cannot be honoured safely."""


@dataclass
class RenderPlan:
    clip_start: float
    clip_end: float
    canvas: tuple[int, int]
    fps: int
    quality: str
    crf: int
    preset: str
    audio_bitrate: str
    duration: float
    keep_ranges: list[tuple[float, float]]
    captions_enabled: bool
    normalize_audio: bool
    face_crop: tuple[float, float] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "clip_start": round(self.clip_start, 3),
            "clip_end": round(self.clip_end, 3),
            "duration_seconds": round(self.duration, 3),
            "width": self.canvas[0],
            "height": self.canvas[1],
            "fps": self.fps,
            "quality": self.quality,
            "crf": self.crf,
            "x264_preset": self.preset,
            "audio_bitrate": self.audio_bitrate,
            "captions": self.captions_enabled,
            "normalize_audio": self.normalize_audio,
            "silence_cuts": [[round(a, 3), round(b, 3)] for a, b in self.keep_ranges]
            if len(self.keep_ranges) > 1
            else [],
            "face_crop": list(self.face_crop) if self.face_crop else None,
            "warnings": self.warnings,
        }


def validate_bounds(start: float, end: float, media_duration: float | None) -> tuple[float, float]:
    if media_duration is None or media_duration <= 0:
        raise RenderValidationError("Source media duration is unknown; cannot render a clip.")
    if start < 0:
        raise RenderValidationError("Clip start cannot be negative.")
    if end <= start:
        raise RenderValidationError("Clip end must be greater than clip start.")
    if start >= media_duration:
        raise RenderValidationError("Clip start is beyond the end of the source media.")
    clipped_end = min(end, media_duration)
    if clipped_end - start < 1.0:
        raise RenderValidationError("Clip must be at least 1 second long.")
    if clipped_end - start > settings.max_duration_seconds:
        raise RenderValidationError(
            f"Clip length exceeds the configured maximum of {settings.max_duration_seconds}s."
        )
    return start, round(clipped_end, 3)


def resolve_canvas(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    if resolution not in RESOLUTION_HEIGHTS:
        raise RenderValidationError(f"Unsupported resolution preset: {resolution}")
    if aspect_ratio not in {"9:16", "1:1", "16:9", "4:5"}:
        raise RenderValidationError(f"Unsupported aspect ratio: {aspect_ratio}")
    width, height = canvas_for_ratio(aspect_ratio, resolution)
    return (width - width % 2, height - height % 2)


def plan_render(
    *,
    media_info: MediaInfo,
    clip_start: float,
    clip_end: float,
    aspect_ratio: str,
    clip_settings: dict[str, Any],
    caption_lines: list[CaptionLine] | None = None,
) -> RenderPlan:
    start, end = validate_bounds(clip_start, clip_end, media_info.duration_seconds)
    resolution = clip_settings.get("resolution", "1080p")
    quality = clip_settings.get("quality", "standard")
    if quality not in QUALITY_PRESETS:
        raise RenderValidationError(f"Unsupported quality preset: {quality}")
    canvas = resolve_canvas(aspect_ratio, resolution)
    fps = int(clip_settings.get("fps", 30))
    if fps < 15 or fps > 60:
        raise RenderValidationError("fps must be between 15 and 60")

    warnings: list[str] = []
    if media_info.width and media_info.height:
        source_ratio = media_info.width / media_info.height
        target_ratio = canvas[0] / canvas[1]
        if abs(source_ratio - target_ratio) > 0.02:
            warnings.append(
                "Source aspect ratio differs from the output; the frame will be cropped to fit."
                if source_ratio > target_ratio
                else "Source is narrower than the output ratio; padding may appear."
            )
        if canvas[0] > (media_info.width or canvas[0]) * 1.6:
            warnings.append("Output resolution is much larger than the source; quality will be upscaled.")

    keep_ranges: list[tuple[float, float]] = [(start, end)]
    duration = end - start
    if clip_settings.get("remove_silence"):
        from app.services.highlights import compute_silence_removal
        warnings.append(
            "Silence removal re-encodes speech segments and concatenates them; timing jumps at cuts."
        )
        _ = compute_silence_removal  # actual ranges computed during rendering (needs the file)
        keep_ranges = [(start, end)]

    if not media_info.has_audio and clip_settings.get("normalize_audio", True):
        warnings.append("Source has no audio track; audio normalisation skipped.")

    music = clip_settings.get("background_music", "none")
    if music not in MUSIC_CHOICES:
        raise RenderValidationError(f"Unknown background music option: {music}")

    if caption_lines is not None:
        warnings.extend(estimate_reading_speed(caption_lines))

    return RenderPlan(
        clip_start=start,
        clip_end=end,
        canvas=canvas,
        fps=fps,
        quality=quality,
        crf=QUALITY_PRESETS[quality]["crf"],
        preset=QUALITY_PRESETS[quality]["preset"],
        audio_bitrate=QUALITY_PRESETS[quality]["audio_bitrate"],
        duration=round(duration, 3),
        keep_ranges=keep_ranges,
        captions_enabled=bool(clip_settings.get("captions_enabled", True)) and bool(caption_lines),
        normalize_audio=bool(clip_settings.get("normalize_audio", True)) and media_info.has_audio,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- face reframe
def analyze_face_crop(source: Path, *, start: float, end: float, target_ratio: float) -> tuple[float, float] | None:
    """Return (start_x_fraction, end_x_fraction) for a smoothed horizontal pan.

    Returns None when no subject is detected reliably, in which case callers fall
    back to centre cropping.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover - optional dependency
        return None
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    if not cascade_path.exists():
        return None
    cascade = cv2.CascadeClassifier(str(cascade_path))
    if cascade.empty():
        return None
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        return None
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
        if width <= 0 or frame_count <= 0:
            return None
        source_ratio = width / (capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1)
        if target_ratio >= source_ratio:
            return None  # portrait target from portrait source: no horizontal pan needed
        sample_step = max(1, int(fps / 2))
        first_frame = int(start * fps)
        last_frame = int(min(end * fps, frame_count))
        positions: list[tuple[float, float]] = []
        for frame_index in range(first_frame, last_frame, sample_step):
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                continue
            small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
            if len(faces) == 0:
                continue
            x, y, w, h = max(faces, key=lambda rect: rect[2] * rect[3])
            centre_x = (x + w / 2) / (small.shape[1])
            centre_y = (y + h / 2) / (small.shape[0])
            positions.append((centre_x, centre_y))
            if len(positions) >= 240:
                break
        if len(positions) < 5:
            return None
        xs = np.array([p[0] for p in positions], dtype=float)
        median = float(np.median(xs))
        cleaned = np.where(np.abs(xs - median) > 0.25, median, xs)
        kernel = max(1, min(9, len(cleaned) // 3 * 2 + 1))
        smooth = np.convolve(cleaned, np.ones(kernel) / kernel, mode="same")
        half_window = (target_ratio / source_ratio) / 2
        clamp = lambda value: float(min(max(value, half_window), 1 - half_window))  # noqa: E731
        return (clamp(float(smooth[0])), clamp(float(smooth[-1])))
    except Exception as exc:  # noqa: BLE001 - analysis must never break rendering
        logger.info("Face analysis unavailable: %s", exc)
        return None
    finally:
        capture.release()


def _crop_expression(face_crop: tuple[float, float] | None, duration: float) -> str | None:
    if not face_crop:
        return None
    start_x, end_x = face_crop
    if abs(start_x - end_x) < 0.02:
        return f"clip((iw*{start_x:.4f}-ow/2)/max(iw-ow,1),0,1)*max(iw-ow,0)"
    # Slow linear pan with easing over the clip; clamped into the valid crop window.
    return (
        f"clip((iw*({start_x:.4f}+({end_x - start_x:.4f})*min(t/{max(duration, 0.1):.3f},1))-ow/2)"
        "/max(iw-ow,1),0,1)*max(iw-ow,0)"
    )


# --------------------------------------------------------------------------- ASS assembly
def build_caption_document(
    *,
    lines: list[CaptionLine],
    style: dict[str, Any],
    canvas: tuple[int, int],
    duration: float,
    overlay_text: str | None = None,
    overlay_position: str = "top",
    intro_text: str | None = None,
    outro_text: str | None = None,
) -> str:
    """One ASS document holding captions, text overlays and intro/outro cards."""
    document = build_ass(lines, style, canvas=canvas, duration=duration, title="ClipFoam clip")
    extra_events: list[str] = []
    width, height = canvas
    font_size = max(20, int(round(height * 0.045)))

    def _card(text: str, begin: float, end: float, size: int, color: str) -> str:
        return (
            f"Dialogue: 1,{format_ass_timestamp(begin)},{format_ass_timestamp(end)},"
            f"ClipFoamCard,,0,0,0,,{{\\an5\\pos({width // 2},{height // 2})\\fad(150,150)}}{text}"
        )

    if overlay_text:
        position = "top" if overlay_position == "top" else "bottom"
        y = int(height * 0.12) if position == "top" else int(height * 0.88)
        extra_events.append(
            f"Dialogue: 2,{format_ass_timestamp(0)},{format_ass_timestamp(duration)},ClipFoamOverlay,,0,0,0,,"
            f"{{\\an5\\pos({width // 2},{y})\\fad(200,200)}}{overlay_text}"
        )
    if intro_text:
        extra_events.append(
            _card(intro_text, 0.0, min(1.6, duration * 0.25), font_size, "#F5F7F6")
        )
    if outro_text:
        start = max(0.0, duration - min(1.8, duration * 0.25))
        extra_events.append(_card(outro_text, start, duration, font_size, "#39FF14"))

    if not extra_events:
        return document

    style_block = (
        "Style: ClipFoamOverlay,Inter,%d,&H00FFFFFF,&H0039FF14,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,"
        "3,3,1,5,40,40,40,1\n"
        "Style: ClipFoamCard,Inter,%d,&H00FFFFFF,&H0039FF14,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,"
        "1,4,2,5,60,60,60,1\n" % (max(28, int(font_size * 0.95)), max(30, int(font_size * 1.1)))
    )
    document = document.replace(
        "\n[Events]", "\n" + style_block + "\n[Events]", 1
    )
    return document + "\n".join(extra_events) + "\n"


# --------------------------------------------------------------------------- command builder
@dataclass
class RenderInputs:
    source_path: Path
    output_path: Path
    work_dir: Path
    plan: RenderPlan
    captions_path: Path | None = None
    logo_path: Path | None = None
    music_path: Path | None = None
    has_audio: bool = True
    face_crop: tuple[float, float] | None = None
    keep_ranges: list[tuple[float, float]] | None = None


def build_render_command(inputs: RenderInputs) -> list[str]:
    plan = inputs.plan
    keep_ranges = inputs.keep_ranges or plan.keep_ranges
    args: list[str] = [
        settings.ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(inputs.source_path),
    ]
    logo_index = None
    if inputs.logo_path:
        args += ["-i", str(inputs.logo_path)]
        logo_index = 1
    music_index = None
    if inputs.music_path and plan.normalize_audio:
        args += ["-stream_loop", "-1", "-i", str(inputs.music_path)]
        music_index = 2 if logo_index else 1

    width, height = plan.canvas
    filters: list[str] = []
    concat_enabled = len(keep_ranges) > 1 and inputs.has_audio

    if concat_enabled:
        video_labels: list[str] = []
        audio_labels: list[str] = []
        for index, (start, end) in enumerate(keep_ranges):
            filters.append(
                f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{index}]"
            )
            filters.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{index}]"
            )
            video_labels.append(f"[v{index}]")
            audio_labels.append(f"[a{index}]")
        inputs_count = len(keep_ranges)
        interleaved = "".join(f"{video_labels[i]}{audio_labels[i]}" for i in range(inputs_count))
        filters.append(f"{interleaved}concat=n={inputs_count}:v=1:a=1[vcat][acat]")
        video_src, audio_src = "[vcat]", "[acat]"
    else:
        start, end = keep_ranges[0]
        filters.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[vbase]")
        video_src = "[vbase]"
        audio_src = None
        if inputs.has_audio:
            filters.append(f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[abase]")
            audio_src = "[abase]"

    # Scale-to-cover then crop to the target canvas (center or face-aware pan).
    filters.append(
        f"{video_src}scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-ow)/2:(ih-oh)/2,setsar=1[vcover]"
    )
    crop_expr = _crop_expression(inputs.face_crop, plan.duration)
    if crop_expr:
        filters.append(f"[vcover]crop={width}:{height}:{crop_expr}:(ih-oh)/2,setsar=1[vcrop]")
        video_current = "[vcrop]"
    else:
        video_current = "[vcover]"

    if inputs.captions_path is not None:
        name = inputs.captions_path.name  # relative to work_dir (cwd) → no escaping needed
        filters.append(f"{video_current}subtitles={name}[vsub]")
        video_current = "[vsub]"

    if logo_index is not None:
        filters.append(f"[{logo_index}:v]scale=200:-1[logo]")
        filters.append(
            f"{video_current}[logo]overlay=W-w-32:H-h-32:enable='between(t,0,{plan.duration:.2f})'[voverlay]"
        )
        video_current = "[voverlay]"

    filters.append(f"{video_current}format=yuv420p[vout]")

    if inputs.has_audio and audio_src:
        audio_chain = audio_src
        if plan.normalize_audio:
            filters.append(f"{audio_chain}loudnorm=I=-16:TP=-1.5:LRA=11[aann]")
            audio_chain = "[aann]"
        if music_index is not None:
            gain = float(plan.__dict__.get("music_gain_db", -18.0) or -18.0)
            filters.append(f"[{music_index}:a]volume={gain}dB,atrim=0:{plan.duration:.3f}[abg]")
            filters.append(
                f"{audio_chain}[abg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[amix]"
            )
            audio_chain = "[amix]"
        filters.append(f"{audio_chain}aresample=48000[aout]")

    args += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    if inputs.has_audio:
        args += ["-map", "[aout]"]

    args += [
        "-c:v",
        "libx264",
        "-preset",
        plan.preset,
        "-crf",
        str(plan.crf),
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(plan.fps),
        "-g",
        str(plan.fps * 2),
        "-movflags",
        "+faststart",
    ]
    if inputs.has_audio:
        args += ["-c:a", "aac", "-b:a", plan.audio_bitrate, "-ac", "2", "-ar", "48000"]
    else:
        args += ["-an"]
    args += ["-t", f"{plan.duration:.3f}", str(inputs.output_path)]
    return args


def render_clip(inputs: RenderInputs, *, timeout: int | None = None) -> dict[str, Any]:
    if disk_free_mb(inputs.work_dir) < settings.min_free_disk_mb:
        raise RenderValidationError(
            f"Not enough free disk space to render ({settings.min_free_disk_mb} MB required)."
        )
    command = build_render_command(inputs)
    run_command(command, timeout=timeout or settings.render_timeout_seconds, cwd=inputs.work_dir)
    if not inputs.output_path.exists() or inputs.output_path.stat().st_size == 0:
        raise MediaError("FFmpeg reported success but produced no output file")
    info = probe_media(inputs.output_path)
    return {
        "path": inputs.output_path,
        "size_bytes": inputs.output_path.stat().st_size,
        "duration_seconds": info.duration_seconds,
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "has_audio": info.has_audio,
        "command": _redact(command),
    }


def _redact(command: Sequence[str]) -> list[str]:
    return [part if len(part) < 400 else part[:400] + "…" for part in command]


def make_silence_ranges(
    source_path: Path, *, clip_start: float, clip_end: float, min_silence: float
) -> list[tuple[float, float]]:
    from app.services.highlights import compute_silence_removal
    from app.services.media import detect_silences

    try:
        silences = detect_silences(source_path, min_seconds=min_silence)
    except MediaError as exc:
        logger.info("Silence detection failed: %s", exc)
        return [(clip_start, clip_end)]
    ranges = compute_silence_removal(
        silences, clip_start=clip_start, clip_end=clip_end, min_silence=min_silence
    )
    if not ranges:
        return [(clip_start, clip_end)]
    return ranges


def music_path_for(choice: str) -> Path | None:
    if choice in {"", "none"}:
        return None
    library = Path(settings.local_storage_path).parent / "assets" / "music"
    if not library.exists():
        return None
    for suffix in (".mp3", ".wav", ".m4a", ".ogg"):
        candidate = library / f"{choice}{suffix}"
        if candidate.exists():
            return candidate
    return None


def validate_music_choice(choice: str) -> str | None:
    if choice in {"", "none"}:
        return None
    if music_path_for(choice) is None:
        return (
            "No licensed background track is installed for "
            f"'{choice}'. Add {choice}.mp3 to <data>/assets/music/ or select 'none'. "
            "ClipFoam does not bundle third-party music."
        )
    return None


def safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


_ = (CaptionError, Iterable, math, assign_ass_color)
