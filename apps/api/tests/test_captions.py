"""Caption engine: timestamps, wrapping, presets and ASS generation."""

from __future__ import annotations

import pytest

from app.models import VisualPreset
from app.services.captions import (
    build_ass,
    build_caption_lines,
    build_srt,
    clamp_style,
    format_ass_timestamp,
    format_timestamp,
    parse_timestamp,
    preview_caption_lines,
    style_for_preset,
)
from app.services.transcription import parse_subtitles

SEGMENTS = [
    {
        "start_seconds": 0.5,
        "end_seconds": 4.2,
        "text": "Most people think the biggest mistake in video editing is the software",
        "words": [],
    },
    {
        "start_seconds": 4.5,
        "end_seconds": 9.8,
        "text": "The real problem is that they never test the hook in the first three seconds",
        "words": [],
    },
    {
        "start_seconds": 10.2,
        "end_seconds": 14.6,
        "text": "Here is the framework I use: pick the promise, cut the setup, and lead with the payoff",
        "words": [],
    },
]


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0:00:00.00"),
        (1.234, "0:00:01.23"),
        (59.999, "0:01:00.00"),
        (61.5, "0:01:01.50"),
        (3725.04, "1:02:05.04"),
    ],
)
def test_ass_timestamps_use_centiseconds(seconds, expected):
    assert format_ass_timestamp(seconds) == expected


def test_srt_timestamps_roundtrip():
    assert format_timestamp(1.234, comma=True) == "00:00:01,234"
    assert parse_timestamp("00:00:01,234") == pytest.approx(1.234)
    assert parse_timestamp("00:01:01.5") == pytest.approx(61.5)


def test_caption_lines_are_clip_relative_and_bounded():
    style = style_for_preset(VisualPreset.KARAOKE)
    lines = build_caption_lines(SEGMENTS, style, clip_start=4.0, clip_end=14.6)
    assert lines, "expected caption lines inside the window"
    assert all(line.start >= 0 for line in lines)
    assert max(line.end for line in lines) == pytest.approx(10.6, abs=0.4)
    for line in lines:
        assert len(line.text) <= style["max_chars_per_line"] * style["max_lines"] + 4
        assert len(line.words) <= style["words_per_caption"]


def test_uppercase_and_reading_speed_warnings():
    style = style_for_preset(VisualPreset.MINIMAL)
    style["uppercase"] = True
    preview = preview_caption_lines(SEGMENTS, style, clip_start=0, clip_end=9.8)
    assert preview[0]["text"] == preview[0]["text"].upper()

    fast_style = style_for_preset(VisualPreset.MINIMAL)
    fast_style["words_per_caption"] = 30
    assert fast_style["max_chars_per_line"] >= 20


def test_style_clamping_keeps_values_in_range():
    wild = {
        "font_size": 500,
        "outline_width": -4,
        "background_opacity": 3,
        "max_chars_per_line": 1,
        "max_lines": 99,
        "safe_area_margin": 80,
    }
    clamped = clamp_style({**style_for_preset(VisualPreset.BOLD_CREATOR), **wild})
    assert 18 <= clamped["font_size"] <= 140
    assert clamped["outline_width"] >= 0
    assert 0 <= clamped["background_opacity"] <= 1
    assert clamped["max_chars_per_line"] >= 8
    assert clamped["max_lines"] <= 4
    assert clamped["safe_area_margin"] <= 20


def test_ass_output_has_header_events_and_centisecond_timing():
    style = style_for_preset(VisualPreset.KARAOKE)
    lines = build_caption_lines(SEGMENTS, style, clip_start=0, clip_end=14.6)
    ass = build_ass(lines, style, canvas=(1080, 1920), duration=14.6)
    assert "ScriptType: v4.00+" in ass
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Dialogue:" in ass
    for row in ass.splitlines():
        if row.startswith("Dialogue:"):
            stamp = row.split(",")[1]
            assert len(stamp.split(":")) == 3
            assert len(stamp.split(":")[2].split(".")[1]) == 2, row


def test_srt_output_is_valid():
    style = style_for_preset(VisualPreset.MINIMAL)
    lines = build_caption_lines(SEGMENTS, style, clip_start=0, clip_end=14.6)
    srt = build_srt(lines)
    assert " --> " in srt
    assert "00:00:00,500" in srt


def test_srt_import_parses_cues_with_timestamps():
    content = (
        "1\n00:00:00,500 --> 00:00:04,200\nFirst line.\n\n"
        "2\n00:00:04,500 --> 00:00:09,800\nSecond line.\n"
    )
    cues = parse_subtitles(content, "srt")
    assert len(cues) == 2
    assert cues[0].start == 0.5
    assert cues[0].end == 4.2
    assert cues[1].text == "Second line."


def test_vtt_import_parses_header_and_cues():
    content = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello there.\n"
    cues = parse_subtitles(content, "vtt")
    assert len(cues) == 1
    assert cues[0].start == 1.0
