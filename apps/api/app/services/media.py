"""FFmpeg/FFprobe media helpers.

All external commands run with argument arrays (never a shell string) and a
hard timeout. Metadata extraction prefers ``ffprobe`` and falls back to PyAV so
a developer setup without system ffmpeg still reports real values instead of
inventing them.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class MediaError(RuntimeError):
    """Raised when FFmpeg/FFprobe cannot complete an operation."""


@dataclass
class MediaInfo:
    duration_seconds: float
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool = False
    bit_rate: int | None = None
    format_name: str | None = None
    size_bytes: int | None = None
    probe_source: str = "ffprobe"
    raw: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- process runner
def run_command(args: list[str], *, timeout: int | None = None, cwd: Path | None = None) -> str:
    timeout = timeout or settings.render_timeout_seconds
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except FileNotFoundError as exc:
        raise MediaError(f"Executable not found: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"Command timed out after {timeout}s: {args[0]}") from exc
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-8:]
        raise MediaError(f"{Path(args[0]).name} failed: {' | '.join(tail)[:800]}")
    return completed.stdout


def ffmpeg_available() -> bool:
    return shutil.which(settings.ffmpeg_path()) is not None or Path(settings.ffmpeg_path()).exists()


def ffprobe_available() -> bool:
    return settings.has_ffprobe()


def _parse_fps(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        if "/" in value:
            return round(float(Fraction(value)), 3)
        return round(float(value), 3)
    except (ValueError, ZeroDivisionError):
        return None


# --------------------------------------------------------------------------- probing
def probe_media(path: Path) -> MediaInfo:
    if ffprobe_available():
        try:
            return _probe_with_ffprobe(path)
        except MediaError as exc:
            logger.warning("ffprobe failed (%s); falling back to PyAV", exc)
    return _probe_with_pyav(path)


def _probe_with_ffprobe(path: Path) -> MediaInfo:
    out = run_command(
        [
            settings.ffprobe_path(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=120,
    )
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration_raw = fmt.get("duration") or (video or {}).get("duration") or 0
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        raise MediaError("Unable to determine media duration")
    return MediaInfo(
        duration_seconds=round(duration, 3),
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        fps=_parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")) if video else None,
        video_codec=(video or {}).get("codec_name"),
        audio_codec=(audio or {}).get("codec_name"),
        has_audio=audio is not None,
        bit_rate=int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        format_name=fmt.get("format_name"),
        size_bytes=int(fmt["size"]) if str(fmt.get("size", "")).isdigit() else path.stat().st_size,
        probe_source="ffprobe",
        raw={"format": fmt, "streams": streams},
    )


def _probe_with_pyav(path: Path) -> MediaInfo:
    try:
        import av
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise MediaError("Neither ffprobe nor PyAV is available for media probing") from exc
    try:
        with av.open(str(path)) as container:
            video = next((s for s in container.streams if s.type == "video"), None)
            audio = next((s for s in container.streams if s.type == "audio"), None)
            duration = float(container.duration / 1_000_000) if container.duration else 0.0
            if duration <= 0:
                durations = [
                    float(s.duration * s.time_base)
                    for s in container.streams
                    if s.duration and s.time_base
                ]
                duration = max(durations) if durations else 0.0
            if duration <= 0:
                raise MediaError("Unable to determine media duration")
            fps = None
            if video is not None and video.average_rate:
                fps = round(float(video.average_rate), 3)
            return MediaInfo(
                duration_seconds=round(duration, 3),
                width=getattr(video, "codec_context", None).width if video else None,
                height=getattr(video, "codec_context", None).height if video else None,
                fps=fps,
                video_codec=video.codec_context.name if video else None,
                audio_codec=audio.codec_context.name if audio else None,
                has_audio=audio is not None,
                format_name=container.format.name,
                size_bytes=path.stat().st_size,
                probe_source="pyav",
                raw={"format": container.format.name},
            )
    except MediaError:
        raise
    except Exception as exc:  # noqa: BLE001 - container demux errors
        raise MediaError(f"Unable to read media metadata: {exc}") from exc


# --------------------------------------------------------------------------- thumbnails
def generate_thumbnail(source: Path, target: Path, *, at_seconds: float = 1.0, width: int = 640) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    args = [
        settings.ffmpeg_path(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(at_seconds, 0):.3f}",
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={width}:-2",
        "-q:v",
        "3",
        str(target),
    ]
    try:
        run_command(args, timeout=120)
    except MediaError:
        # Some streams cannot seek; retry from the first frame.
        run_command(
            [
                settings.ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                str(target),
            ],
            timeout=120,
        )
    return target


def extract_audio(source: Path, target: Path) -> Path:
    """Extract a 16 kHz mono WAV copy for transcription engines."""
    target.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            settings.ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(target),
        ],
        timeout=settings.render_timeout_seconds,
    )
    return target


# --------------------------------------------------------------------------- analysis
SILENCE_RE = re.compile(r"silence_(start|end): ([0-9.]+)")


def detect_silences(source: Path, *, noise_db: int = -32, min_seconds: float = 0.6) -> list[tuple[float, float]]:
    """Return (start, end) silence windows using FFmpeg's silencedetect filter."""
    args = [
        settings.ffmpeg_path(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(source),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_seconds}",
        "-f",
        "null",
        "-",
    ]
    timeout = min(settings.render_timeout_seconds, 600)
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Silence detection timed out") from exc
    output = f"{completed.stdout}\n{completed.stderr}"
    windows: list[tuple[float, float]] = []
    current_start: float | None = None
    for match in SILENCE_RE.finditer(output):
        kind, value = match.group(1), float(match.group(2))
        if kind == "start":
            current_start = value
        elif current_start is not None:
            windows.append((current_start, value))
            current_start = None
    return windows


def detect_scenes(source: Path, *, threshold: float = 27.0, min_seconds: float = 1.5) -> list[float]:
    """Scene cut timestamps via PySceneDetect ContentDetector. Empty list on failure."""
    try:
        from scenedetect import ContentDetector, SceneManager, open_video
    except ImportError:  # pragma: no cover - optional dependency
        return []
    try:
        video = open_video(str(source))
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=int(min_seconds * 30)))
        manager.detect_scenes(video, show_progress=False)
        return [round(scene[0].get_seconds(), 3) for scene in manager.get_scene_list()]
    except Exception as exc:  # noqa: BLE001 - codec/analysis errors are non fatal
        logger.info("Scene detection unavailable: %s", exc)
        return []


def measure_loudness(source: Path) -> float | None:
    """Integrated loudness (LUFS) via loudnorm analysis, or None if unavailable."""
    args = [
        settings.ffmpeg_path(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(source),
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    except subprocess.TimeoutExpired:
        return None
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", completed.stderr or "", re.S)
    if not match:
        return None
    try:
        return float(json.loads(match.group(0))["input_i"])
    except (ValueError, KeyError):
        return None
