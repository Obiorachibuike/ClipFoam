"""Speech-to-text providers plus SRT/VTT/JSON transcript parsing.

The default engine is faster-whisper (local, no API key). When the package or a
model is unavailable, the failure is surfaced verbatim — ClipFoam never invents
transcript text. Users can still attach a real transcript they already have via
the import endpoint, which keeps the rest of the pipeline usable.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.errors import ProviderUnavailable, UnsupportedMedia
from app.services.captions import CaptionError, parse_timestamp

logger = logging.getLogger(__name__)


@dataclass
class SegmentData:
    index: int
    start: float
    end: float
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TranscriptionResult:
    segments: list[SegmentData]
    language: str | None
    duration_seconds: float | None
    provider: str
    model: str | None
    raw: dict[str, Any] = field(default_factory=dict)


class TranscriptionProvider(Protocol):
    id: str

    def available(self) -> tuple[bool, str | None]: ...

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> TranscriptionResult: ...


# --------------------------------------------------------------------------- faster-whisper
_model_cache: dict[tuple[str, str, str], Any] = {}
_model_lock = threading.Lock()


class FasterWhisperProvider:
    id = "faster_whisper"

    def available(self) -> tuple[bool, str | None]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return (
                False,
                "faster-whisper is not installed. Install it with "
                "`pip install -r requirements-local-ai.txt` (needs ~1-3 GB RAM per model), "
                "or import an existing transcript (SRT/VTT) instead.",
            )
        return True, None

    def _load_model(self):
        from faster_whisper import WhisperModel

        device = settings.whisper_device
        if device == "auto":
            device = "cuda" if _cuda_available() else "cpu"
        compute_type = settings.whisper_compute_type
        if device == "cpu" and compute_type in {"float16", "int8_float16"}:
            compute_type = "int8"
        key = (settings.whisper_model, device, compute_type)
        with _model_lock:
            if key not in _model_cache:
                try:
                    _model_cache[key] = WhisperModel(
                        settings.whisper_model,
                        device=device,
                        compute_type=compute_type,
                        download_root=settings.whisper_download_root,
                    )
                except Exception as exc:  # noqa: BLE001 - download/runtime failures
                    raise ProviderUnavailable(
                        "Unable to load the faster-whisper model "
                        f"'{settings.whisper_model}': {exc}. Check network access to the model "
                        "registry or pre-download the model into CF_WHISPER_DOWNLOAD_ROOT.",
                        code="transcription_model_unavailable",
                    ) from exc
            return _model_cache[key]

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> TranscriptionResult:
        ok, reason = self.available()
        if not ok:
            raise ProviderUnavailable(reason or "Transcription engine unavailable", code="transcription_unavailable")
        model = self._load_model()
        try:
            segments_iter, info = model.transcribe(
                str(audio_path),
                language=language,
                beam_size=settings.whisper_beam_size,
                word_timestamps=settings.whisper_word_timestamps,
                vad_filter=True,
                condition_on_previous_text=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"Transcription failed: {exc}", code="transcription_failed") from exc

        segments: list[SegmentData] = []
        for index, segment in enumerate(segments_iter):
            words = []
            for word in getattr(segment, "words", None) or []:
                words.append(
                    {
                        "word": word.word.strip(),
                        "start": round(float(word.start), 3),
                        "end": round(float(word.end), 3),
                        "probability": round(float(getattr(word, "probability", 0.0)), 4),
                    }
                )
            text = (segment.text or "").strip()
            if not text:
                continue
            segments.append(
                SegmentData(
                    index=len(segments),
                    start=round(float(segment.start), 3),
                    end=round(float(segment.end), 3),
                    text=text,
                    words=words,
                )
            )
        if not segments:
            raise ProviderUnavailable(
                "The transcription engine returned no speech segments for this media.",
                code="transcription_empty",
            )
        return TranscriptionResult(
            segments=segments,
            language=getattr(info, "language", None) or language,
            duration_seconds=getattr(info, "duration", None),
            provider=self.id,
            model=settings.whisper_model,
            raw={"language_probability": float(getattr(info, "language_probability", 0.0) or 0.0)},
        )


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001 - optional dependency
        return False


def get_transcription_provider() -> TranscriptionProvider:
    if settings.transcription_provider == "manual":
        return ManualProvider()
    return FasterWhisperProvider()


class ManualProvider:
    """Explicit 'no engine' provider: transcription must be imported by the user."""

    id = "manual"

    def available(self) -> tuple[bool, str | None]:
        return (
            False,
            "Manual transcription mode: upload or paste an existing SRT/VTT/JSON transcript. "
            "Set CF_TRANSCRIPTION_PROVIDER=faster_whisper to enable the local engine.",
        )

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> TranscriptionResult:
        raise ProviderUnavailable(self.available()[1] or "Manual mode", code="transcription_manual_mode")


# --------------------------------------------------------------------------- parsing
_SRT_BLOCK = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}|\d{1,2}:\d{2}[,.]\d{1,3})(?P<rest>[^\n]*)\n"
    r"(?P<text>.*?)(?=\n\s*\n|\Z)",
    re.S,
)


def parse_subtitles(content: str, fmt: str) -> list[SegmentData]:
    fmt = fmt.lower()
    if fmt == "json":
        return _parse_json_transcript(content)
    if fmt in {"srt", "vtt"}:
        return _parse_timed_text(content)
    if fmt == "plain":
        return _parse_plain_text(content)
    raise UnsupportedMedia(f"Unsupported transcript format: {fmt}")


def _clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)  # strip vtt/srt inline tags
    text = re.sub(r"\{\\[^}]*\}", "", text)  # strip ASS override blocks
    return re.sub(r"\s+", " ", text).strip()


def _parse_timed_text(content: str) -> list[SegmentData]:
    segments: list[SegmentData] = []
    for match in _SRT_BLOCK.finditer(content.replace("\r\n", "\n")):
        try:
            start = parse_timestamp(match.group("start"))
            end = parse_timestamp(match.group("end"))
        except CaptionError:
            continue
        text = _clean_text(match.group("text"))
        if not text:
            continue
        if end <= start:
            end = start + 1.0
        segments.append(
            SegmentData(index=len(segments), start=round(start, 3), end=round(end, 3), text=text)
        )
    if not segments:
        raise UnsupportedMedia(
            "No timestamped cues were found. Expected SRT/VTT cues like `00:00:01,000 --> 00:00:03,500`."
        )
    return sorted(segments, key=lambda seg: seg.start)


def _parse_json_transcript(content: str) -> list[SegmentData]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise UnsupportedMedia(f"Invalid JSON transcript: {exc.msg}") from exc
    raw_segments = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(raw_segments, list):
        raise UnsupportedMedia("JSON transcript must contain a `segments` array")
    segments: list[SegmentData] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", item.get("start_seconds", 0)))
            end = float(item.get("end", item.get("end_seconds", start + 1)))
        except (TypeError, ValueError):
            continue
        text = _clean_text(str(item.get("text", "")))
        if not text:
            continue
        words = []
        for word in item.get("words") or []:
            if not isinstance(word, dict):
                continue
            try:
                words.append(
                    {
                        "word": str(word.get("word") or word.get("text") or "").strip(),
                        "start": float(word.get("start", start)),
                        "end": float(word.get("end", end)),
                    }
                )
            except (TypeError, ValueError):
                continue
        segments.append(
            SegmentData(
                index=len(segments),
                start=round(start, 3),
                end=round(max(end, start + 0.2), 3),
                text=text,
                words=[w for w in words if w["word"]],
            )
        )
    if not segments:
        raise UnsupportedMedia("JSON transcript contained no usable segments")
    return sorted(segments, key=lambda seg: seg.start)


def _parse_plain_text(content: str) -> list[SegmentData]:
    """Plain text has no timing: evenly distribute over the known media duration."""
    raise UnsupportedMedia(
        "Plain text needs media duration context; use the SRT/VTT/JSON import instead so "
        "captions stay synchronised with the source video."
    )


def segments_to_json(segments: list[dict[str, Any]], *, language: str | None, provider: str) -> str:
    return json.dumps(
        {
            "provider": provider,
            "language": language,
            "segments": [
                {
                    "start": seg.get("start_seconds"),
                    "end": seg.get("end_seconds"),
                    "text": seg.get("text"),
                    "words": seg.get("words") or [],
                }
                for seg in segments
            ],
        },
        indent=2,
    )
