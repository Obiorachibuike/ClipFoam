"""Caption engine: transcript -> caption lines -> ASS/SRT/VTT.

Captions are always derived from stored transcript timestamps. The style model
is independent from the source media so the same clip can be re-rendered with a
different look without touching transcription.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.models import VisualPreset

# --------------------------------------------------------------------------- presets
PRESETS: dict[VisualPreset, dict[str, Any]] = {
    VisualPreset.KARAOKE: {
        "font_family": "Inter",
        "font_size": 66,
        "text_color": "#FFFFFF",
        "highlight_color": "#39FF14",
        "outline_color": "#000000",
        "outline_width": 3.5,
        "shadow": 1.0,
        "position": "bottom",
        "alignment": "center",
        "max_chars_per_line": 18,
        "max_lines": 2,
        "uppercase": True,
        "animation": "karaoke",
        "background_opacity": 0.0,
        "words_per_caption": 4,
    },
    VisualPreset.BOLD_CREATOR: {
        "font_family": "Inter",
        "font_size": 62,
        "text_color": "#F5F7F6",
        "highlight_color": "#39FF14",
        "outline_color": "#080B0D",
        "outline_width": 4.0,
        "shadow": 2.0,
        "position": "center",
        "alignment": "center",
        "max_chars_per_line": 16,
        "max_lines": 2,
        "uppercase": True,
        "animation": "pop",
        "background_opacity": 0.0,
        "words_per_caption": 3,
    },
    VisualPreset.MINIMAL: {
        "font_family": "Inter",
        "font_size": 44,
        "text_color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 1.5,
        "shadow": 0.0,
        "position": "bottom",
        "alignment": "center",
        "max_chars_per_line": 34,
        "max_lines": 2,
        "uppercase": False,
        "animation": "fade",
        "background_opacity": 0.0,
        "words_per_caption": 8,
    },
    VisualPreset.BOXED: {
        "font_family": "Inter",
        "font_size": 48,
        "text_color": "#FFFFFF",
        "highlight_color": "#39FF14",
        "outline_color": "#000000",
        "outline_width": 0.0,
        "shadow": 0.0,
        "position": "bottom",
        "alignment": "center",
        "max_chars_per_line": 26,
        "max_lines": 2,
        "uppercase": False,
        "animation": "none",
        "background_opacity": 0.72,
        "words_per_caption": 7,
    },
    VisualPreset.PODCAST: {
        "font_family": "Inter",
        "font_size": 46,
        "text_color": "#F5F7F6",
        "highlight_color": "#39FF14",
        "outline_color": "#101619",
        "outline_width": 2.0,
        "shadow": 1.0,
        "position": "bottom",
        "alignment": "center",
        "max_chars_per_line": 30,
        "max_lines": 3,
        "uppercase": False,
        "animation": "none",
        "background_opacity": 0.55,
        "words_per_caption": 9,
    },
    VisualPreset.NEON: {
        "font_family": "Inter",
        "font_size": 60,
        "text_color": "#39FF14",
        "highlight_color": "#FFFFFF",
        "outline_color": "#0A2E12",
        "outline_width": 5.0,
        "shadow": 4.0,
        "position": "center",
        "alignment": "center",
        "max_chars_per_line": 18,
        "max_lines": 2,
        "uppercase": True,
        "animation": "slide",
        "background_opacity": 0.0,
        "words_per_caption": 4,
    },
    VisualPreset.CLASSIC: {
        "font_family": "DejaVu Sans",
        "font_size": 40,
        "text_color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "outline_color": "#000000",
        "outline_width": 3.0,
        "shadow": 0.0,
        "position": "bottom",
        "alignment": "center",
        "max_chars_per_line": 38,
        "max_lines": 2,
        "uppercase": False,
        "animation": "none",
        "background_opacity": 0.0,
        "words_per_caption": 10,
    },
    VisualPreset.EDITORIAL: {
        "font_family": "Inter",
        "font_size": 52,
        "text_color": "#F5F7F6",
        "highlight_color": "#39FF14",
        "outline_color": "#080B0D",
        "outline_width": 2.5,
        "shadow": 1.0,
        "position": "bottom",
        "alignment": "left",
        "max_chars_per_line": 24,
        "max_lines": 3,
        "uppercase": False,
        "animation": "fade",
        "background_opacity": 0.45,
        "words_per_caption": 7,
    },
}

DEFAULT_STYLE: dict[str, Any] = {
    "preset": VisualPreset.BOLD_CREATOR.value,
    "font_family": "Inter",
    "font_size": 62,
    "text_color": "#F5F7F6",
    "highlight_color": "#39FF14",
    "background_color": "#000000",
    "background_opacity": 0.0,
    "outline_color": "#080B0D",
    "outline_width": 4.0,
    "shadow": 2.0,
    "position": "center",
    "alignment": "center",
    "max_chars_per_line": 16,
    "max_lines": 2,
    "uppercase": True,
    "animation": "pop",
    "safe_area_margin": 8,
    "words_per_caption": 3,
}


class CaptionError(ValueError):
    pass


def style_for_preset(preset: VisualPreset | str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        key = VisualPreset(preset)
    except ValueError as exc:
        raise CaptionError(f"Unknown caption preset: {preset}") from exc
    style = {**DEFAULT_STYLE, **PRESETS[key]}
    style["preset"] = key.value
    if overrides:
        style.update({k: v for k, v in overrides.items() if v is not None})
    return style


# --------------------------------------------------------------------------- time helpers
def format_timestamp(seconds: float, *, comma: bool = True, vtt: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if vtt:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    sep = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def parse_timestamp(value: str) -> float:
    raw = value.strip().replace(",", ".")
    match = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$", raw)
    if not match:
        raise CaptionError(f"Invalid timestamp: {value}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    millis = int((match.group(4) or "0").ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def format_ass_timestamp(seconds: float) -> str:
    """ASS uses centisecond precision (H:MM:SS.cc) — milliseconds would be misparsed."""
    seconds = max(0.0, float(seconds))
    total_cs = int(round(seconds * 100))
    hours, rem = divmod(total_cs, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, centis = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centis:02d}"


def assign_ass_color(hex_color: str, opacity: float = 1.0) -> str:
    """#RRGGBB -> &HaaBBGGRR (ASS/SSA uses BGR with alpha first)."""
    raw = hex_color.lstrip("#")
    if len(raw) == 8:
        raw = raw[:6]
    if len(raw) != 6:
        raise CaptionError(f"Invalid color: {hex_color}")
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    alpha = int(round((1.0 - max(0.0, min(1.0, opacity))) * 255))
    return f"&H{alpha:02X}{bb}{gg}{rr}".upper()


# --------------------------------------------------------------------------- caption lines
@dataclass
class CaptionWord:
    text: str
    start: float
    end: float


@dataclass
class CaptionLine:
    start: float
    end: float
    words: list[CaptionWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()


def _segment_words(
    segment: dict[str, Any], *, fallback_start: float, fallback_end: float
) -> list[CaptionWord]:
    """Word-level timings from the transcript, or an even split over the segment."""
    raw_words = segment.get("words") or []
    words: list[CaptionWord] = []
    for item in raw_words:
        if not isinstance(item, dict):
            continue
        text = str(item.get("word") or item.get("text") or "").strip()
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            continue
        if text:
            words.append(CaptionWord(text=text, start=start, end=max(start, end)))
    if words:
        return words

    tokens = str(segment.get("text", "")).split()
    if not tokens:
        return []
    span = max(0.01, fallback_end - fallback_start)
    step = span / len(tokens)
    return [
        CaptionWord(
            text=token,
            start=fallback_start + i * step,
            end=fallback_start + (i + 1) * step,
        )
        for i, token in enumerate(tokens)
    ]


def build_caption_lines(
    segments: Iterable[dict[str, Any]],
    style: dict[str, Any],
    *,
    clip_start: float = 0.0,
    clip_end: float | None = None,
    pause_split: float = 0.55,
) -> list[CaptionLine]:
    """Group transcript words into caption chunks bounded by words/time.

    Timings are expressed relative to the clip start so the ASS file lines up
    with the rendered (trimmed) video.
    """
    words_per_caption = int(style.get("words_per_caption", 4) or 4)
    max_chars = int(style.get("max_chars_per_line", 20) or 20)
    max_lines = int(style.get("max_lines", 2) or 2)
    char_budget = max_chars * max_lines

    collected: list[CaptionWord] = []
    for segment in segments:
        seg_start = float(segment.get("start_seconds", 0.0))
        seg_end = float(segment.get("end_seconds", seg_start))
        if clip_end is not None and seg_start >= clip_end:
            continue
        if seg_end <= clip_start:
            continue
        collected.extend(
            _segment_words(segment, fallback_start=seg_start, fallback_end=seg_end)
        )

    if not collected:
        return []

    lines: list[CaptionLine] = []
    current: list[CaptionWord] = []
    for word in collected:
        if clip_end is not None and word.start > clip_end:
            break
        if current:
            gap = word.start - current[-1].end
            projected = sum(len(w.text) + 1 for w in current) + len(word.text)
            if gap > pause_split or len(current) >= words_per_caption or projected > char_budget:
                lines.append(CaptionLine(start=current[0].start, end=current[-1].end, words=current))
                current = []
        current.append(word)
    if current:
        lines.append(CaptionLine(start=current[0].start, end=current[-1].end, words=current))

    for line in lines:
        line.start = max(0.0, line.start - clip_start)
        line.end = max(line.start + 0.05, line.end - clip_start)
        for word in line.words:
            word.start = max(0.0, word.start - clip_start)
            word.end = max(word.start + 0.02, word.end - clip_start)
    return lines


def lines_to_text(lines: list[CaptionLine], style: dict[str, Any]) -> list[str]:
    uppercase = bool(style.get("uppercase"))
    max_chars = int(style.get("max_chars_per_line", 20) or 20)
    out: list[str] = []
    for line in lines:
        text = line.text
        if uppercase:
            text = text.upper()
        # Soft-wrap long lines for preview consistency with ASS \N wrapping.
        out.append(text)
        if len(text) > max_chars * 6:
            raise CaptionError("Caption line too long")
    return out


# --------------------------------------------------------------------------- ASS output
def _escape_ass(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def _wrap(text: str, max_chars: int, max_lines: int) -> str:
    words = text.split()
    if not words:
        return text
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        head = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :])
        lines = head + [tail]
    return "\\N".join(lines)


def _layout(words: list[str], max_chars: int, max_lines: int) -> list[list[int]]:
    """Group word indexes into rows based on *visible* character length."""
    rows: list[list[int]] = []
    current: list[int] = []
    length = 0
    for index, word in enumerate(words):
        visible = len(word) + (1 if current else 0)
        if current and length + visible > max_chars:
            rows.append(current)
            current = [index]
            length = len(word)
        else:
            current.append(index)
            length += visible
    if current:
        rows.append(current)
    if len(rows) > max_lines:
        merged = [item for row in rows[max_lines - 1 :] for item in row]
        rows = rows[: max_lines - 1] + [merged]
    return rows


def _render_karaoke_line(
    words: list[str],
    active_index: int,
    *,
    max_chars: int,
    max_lines: int,
    highlight_color: str,
    text_color: str,
) -> str:
    rows = _layout(words, max_chars, max_lines)
    rendered_rows: list[str] = []
    for row in rows:
        tokens: list[str] = []
        for index in row:
            token = _escape_ass(words[index])
            if index == active_index:
                tokens.append(
                    f"{{\\c{highlight_color}\\fscx108\\fscy108}}{token}"
                    f"{{\\c{text_color}\\fscx100\\fscy100}}"
                )
            else:
                tokens.append(token)
        rendered_rows.append(" ".join(tokens))
    return "\\N".join(rendered_rows)


def _render_plain_line(text: str, *, max_chars: int, max_lines: int) -> str:
    words = text.split()
    if not words:
        return _escape_ass(text)
    rows = _layout(words, max_chars, max_lines)
    return "\\N".join(" ".join(_escape_ass(words[index]) for index in row) for row in rows)


def build_ass(
    lines: list[CaptionLine],
    style: dict[str, Any],
    *,
    canvas: tuple[int, int],
    duration: float,
    title: str = "ClipFoam captions",
) -> str:
    """Render an ASS subtitle document with the requested animation style."""
    width, height = canvas
    font_size = int(style.get("font_size", 60))
    # Normalise to the reference 1080px canvas so presets scale across resolutions.
    font_size = max(14, int(round(font_size * height / 1920)))
    alignment_map = {"left": 1, "center": 2, "right": 3}
    position = style.get("position", "bottom")
    alignment = alignment_map.get(style.get("alignment", "center"), 2)
    if position == "bottom":
        an = alignment  # 1..3 = bottom row
    elif position == "top":
        an = alignment + 6  # 7..9 = top row
    else:
        an = alignment + 3  # 4..6 = middle row
    margin = int(style.get("safe_area_margin", 8))
    margin_v = max(24, int(height * margin / 100))
    margin_h = max(24, int(width * max(margin, 5) / 100))

    outline = float(style.get("outline_width", 3))
    shadow = float(style.get("shadow", 1))
    text_color = assign_ass_color(style.get("text_color", "#FFFFFF"))
    highlight_color = assign_ass_color(style.get("highlight_color", "#39FF14"))
    outline_color = assign_ass_color(style.get("outline_color", "#000000"))
    bg_opacity = float(style.get("background_opacity", 0.0) or 0.0)
    border_style = 3 if bg_opacity > 0.02 else 1
    back_color = assign_ass_color(style.get("background_color", "#000000"), bg_opacity)
    uppercase = bool(style.get("uppercase"))
    max_chars = int(style.get("max_chars_per_line", 22) or 22)
    max_lines = int(style.get("max_lines", 2) or 2)
    animation = style.get("animation", "none")
    font_family = str(style.get("font_family") or "DejaVu Sans")

    header = f"""[Script Info]
; Generated by ClipFoam
Title: {_escape_ass(title)}
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ClipFoam,{font_family},{font_size},{text_color},{highlight_color},{outline_color},{back_color},-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},{an},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    fade_ms = 120
    for line in lines:
        if line.end <= line.start:
            continue
        raw_text = line.text
        text = raw_text.upper() if uppercase else raw_text
        if not text.strip():
            continue
        start_ts = format_ass_timestamp(line.start)
        end_ts = format_ass_timestamp(min(line.end, duration))

        if animation == "karaoke" and len(line.words) > 1:
            words = [word.text.upper() if uppercase else word.text for word in line.words]
            for index, word in enumerate(line.words):
                word_start = max(line.start, word.start)
                word_end = min(line.end, max(word.end, word.start + 0.05))
                if word_end <= word_start:
                    continue
                rendered = _render_karaoke_line(
                    words,
                    index,
                    max_chars=max_chars,
                    max_lines=max_lines,
                    highlight_color=highlight_color,
                    text_color=text_color,
                )
                events.append(
                    f"Dialogue: 0,{format_ass_timestamp(word_start)},{format_ass_timestamp(word_end)},"
                    f"ClipFoam,,0,0,0,,{rendered}"
                )
            continue

        prefix = ""
        if animation == "fade":
            prefix = f"{{\\fad({fade_ms},{fade_ms})}}"
        elif animation == "pop":
            prefix = "{\\fad(60,60)\\fscx85\\fscy85\\t(0,120,\\fscx100\\fscy100)}"
        elif animation == "slide":
            prefix = f"{{\\fad(80,60)\\move({width // 2},{int(margin_v * 1.5)},{width // 2},{margin_v},0,160)}}"
        events.append(
            f"Dialogue: 0,{start_ts},{end_ts},ClipFoam,,0,0,0,,{prefix}"
            f"{_render_plain_line(text, max_chars=max_chars, max_lines=max_lines)}"
        )

    if not events:
        return header
    return header + "\n".join(events) + "\n"


# --------------------------------------------------------------------------- SRT / VTT
def build_srt(lines: list[CaptionLine], style: dict[str, Any] | None = None) -> str:
    style = style or {}
    uppercase = bool(style.get("uppercase"))
    max_chars = int(style.get("max_chars_per_line", 42) or 42)
    blocks: list[str] = []
    for index, line in enumerate(lines, start=1):
        text = line.text.upper() if uppercase else line.text
        wrapped = _wrap(text, max_chars, int(style.get("max_lines", 2) or 2)).replace("\\N", "\n")
        blocks.append(
            f"{index}\n{format_timestamp(line.start, comma=True)} --> {format_timestamp(line.end, comma=True)}\n{wrapped}\n"
        )
    return "\n".join(blocks)


def build_vtt(lines: list[CaptionLine], style: dict[str, Any] | None = None) -> str:
    style = style or {}
    uppercase = bool(style.get("uppercase"))
    max_chars = int(style.get("max_chars_per_line", 42) or 42)
    blocks: list[str] = ["WEBVTT", ""]
    for index, line in enumerate(lines, start=1):
        text = line.text.upper() if uppercase else line.text
        wrapped = _wrap(text, max_chars, int(style.get("max_lines", 2) or 2)).replace("\\N", "\n")
        blocks.append(
            f"{index}\n{format_timestamp(line.start, comma=False, vtt=True)} --> {format_timestamp(line.end, comma=False, vtt=True)}\n{wrapped}\n"
        )
    return "\n".join(blocks)


def transcript_to_srt(segments: Iterable[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, seg in enumerate(segments, start=1):
        start = float(seg.get("start_seconds", 0.0))
        end = float(seg.get("end_seconds", start))
        text = str(seg.get("text", "")).strip() or " "
        blocks.append(
            f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{text}\n"
        )
    return "\n".join(blocks)


def transcript_to_vtt(segments: Iterable[dict[str, Any]]) -> str:
    blocks: list[str] = ["WEBVTT", ""]
    for index, seg in enumerate(segments, start=1):
        start = float(seg.get("start_seconds", 0.0))
        end = float(seg.get("end_seconds", start))
        text = str(seg.get("text", "")).strip() or " "
        blocks.append(
            f"{index}\n{format_timestamp(start, vtt=True)} --> {format_timestamp(end, vtt=True)}\n{text}\n"
        )
    return "\n".join(blocks)


# --------------------------------------------------------------------------- preview
def preview_caption_lines(
    segments: list[dict[str, Any]], style: dict[str, Any], *, clip_start: float, clip_end: float
) -> list[dict[str, Any]]:
    lines = build_caption_lines(segments, style, clip_start=clip_start, clip_end=clip_end)
    payload: list[dict[str, Any]] = []
    for line in lines[:400]:
        payload.append(
            {
                "start": round(line.start, 3),
                "end": round(line.end, 3),
                "text": line.text.upper() if style.get("uppercase") else line.text,
                "words": [
                    {"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)}
                    for w in line.words
                ],
            }
        )
    return payload


def canvas_for_ratio(aspect_ratio: str, resolution: str = "1080p") -> tuple[int, int]:
    heights = {"720p": 1280, "1080p": 1920, "1440p": 2560, "2160p": 3840}
    base = heights.get(resolution, 1920)
    ratios = {"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "16:9": 16 / 9}
    ratio = ratios.get(aspect_ratio, 9 / 16)
    if aspect_ratio == "16:9":
        width = base
        height = int(round(base / ratio))
        height -= height % 2
        return width, height
    width = int(round(base * ratio))
    width -= width % 2
    return width, base


def estimate_reading_speed(lines: list[CaptionLine]) -> list[str]:
    """Warn about captions that are too fast/slow to read (CPS guidance 17-25)."""
    warnings: list[str] = []
    for index, line in enumerate(lines, start=1):
        duration = max(0.001, line.end - line.start)
        cps = len(line.text) / duration
        if cps > 34:
            warnings.append(f"Line {index} is very fast ({cps:.0f} chars/sec); consider more words per caption")
        elif cps < 3 and len(line.text) > 12:
            warnings.append(f"Line {index} lingers ({cps:.0f} chars/sec)")
    return warnings[:6]


def words_per_second(lines: list[CaptionLine]) -> float:
    words = sum(len(line.words) for line in lines)
    duration = sum(max(0.0, line.end - line.start) for line in lines)
    if duration <= 0:
        return 0.0
    return round(words / duration, 2)


def min_duration_check(lines: list[CaptionLine], fps: int = 30) -> bool:
    """Every caption must be at least one frame long."""
    return all((line.end - line.start) >= 1 / fps for line in lines)


def style_summary(style: dict[str, Any]) -> str:
    return (
        f"{style.get('preset', 'custom')} / {style.get('font_family')} "
        f"{style.get('font_size')}px / {style.get('animation')} / {style.get('position')}"
    )


def clamp_style(style: dict[str, Any]) -> dict[str, Any]:
    """Defensive bounds for styles coming from stored JSON."""
    out = dict(style)
    out["font_size"] = int(max(18, min(140, int(out.get("font_size", 60) or 60))))
    out["words_per_caption"] = int(max(1, min(12, int(out.get("words_per_caption", 4) or 4))))
    out["max_chars_per_line"] = int(max(8, min(64, int(out.get("max_chars_per_line", 22) or 22))))
    out["max_lines"] = int(max(1, min(4, int(out.get("max_lines", 2) or 2))))
    out["safe_area_margin"] = int(max(0, min(20, int(out.get("safe_area_margin", 8) or 8))))
    out["outline_width"] = float(max(0.0, min(12.0, float(out.get("outline_width", 3.0) or 0.0))))
    out["shadow"] = float(max(0.0, min(8.0, float(out.get("shadow", 1.0) or 0.0))))
    out["background_opacity"] = float(
        max(0.0, min(1.0, float(out.get("background_opacity", 0.0) or 0.0)))
    )
    return out


__all__ = [
    "CaptionError",
    "CaptionLine",
    "CaptionWord",
    "DEFAULT_STYLE",
    "PRESETS",
    "assign_ass_color",
    "build_ass",
    "build_caption_lines",
    "build_srt",
    "build_vtt",
    "canvas_for_ratio",
    "clamp_style",
    "estimate_reading_speed",
    "format_ass_timestamp",
    "format_timestamp",
    "parse_timestamp",
    "preview_caption_lines",
    "style_for_preset",
    "transcript_to_srt",
    "transcript_to_vtt",
]

_ = (math,)  # keep import for numeric helpers used by extensions
