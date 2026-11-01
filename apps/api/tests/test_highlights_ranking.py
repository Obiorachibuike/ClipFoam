"""Deterministic highlight detection and ranking."""

from __future__ import annotations

from app.services.highlights import (
    HighlightOptions,
    detect_highlights,
    merge_candidates,
    strip_filler_words,
)


def _segments(texts: list[str], duration_each: float = 3.0, gap: float = 0.4):
    out = []
    clock = 0.0
    for text in texts:
        out.append(
            {
                "start_seconds": round(clock, 3),
                "end_seconds": round(clock + duration_each, 3),
                "text": text,
                "words": [],
            }
        )
        clock += duration_each + gap
    return out


TALK = _segments(
    [
        "Here is the single biggest pricing mistake I see every week.",
        "You set a price before you know what the customer actually gets.",
        "So the fix is to write down the outcome in one sentence.",
        "Then ask three buyers what that outcome is worth to them.",
        "Most people guess and charge less than the value they deliver.",
        "The safest way to raise prices is to start with new customers.",
        "Grandfather the people who trusted you early, then measure churn.",
        "That is how you price a product with confidence and no guessing.",
        "Alright, we are getting off track, let me find my notes.",
        "Anyway, where was I? Right, pricing with confidence.",
    ]
)


def test_detection_respects_duration_bounds_and_overlap():
    options = HighlightOptions(clip_count=4, min_duration=8, max_duration=20)
    windows = detect_highlights(TALK, duration=40.0, options=options)
    assert windows, "expected at least one candidate"
    assert len(windows) <= 4
    for window in windows:
        assert window.end > window.start
        assert window.end - window.start <= options.max_duration + 0.001
        assert window.end - window.start >= options.min_duration * 0.85
        assert window.excerpt.strip()
        assert window.title and len(window.title) <= 95
        assert window.explanation
        assert window.score > 0

    for i, first in enumerate(windows):
        for second in windows[i + 1 :]:
            overlap = min(first.end, second.end) - max(first.start, second.start)
            assert overlap <= 0.35 * min(first.end - first.start, second.end - second.start) + 0.01


def test_scores_are_sorted_descending():
    windows = detect_highlights(TALK, duration=40.0, options=HighlightOptions(clip_count=5))
    scores = [window.score for window in windows]
    assert scores == sorted(scores, reverse=True)


def test_score_breakdown_contains_every_component():
    windows = detect_highlights(TALK, duration=40.0, options=HighlightOptions(clip_count=2))
    assert windows
    for key in ("hook", "salience", "boundary", "length_fit", "density", "cohesion"):
        assert key in windows[0].breakdown
        assert 0 <= windows[0].breakdown[key] <= 1.5


def test_empty_transcript_returns_no_hidden_candidates():
    assert detect_highlights([], duration=30.0) == []


def test_merge_candidates_keeps_deterministic_entries_when_ai_is_absent():
    windows = detect_highlights(TALK, duration=40.0, options=HighlightOptions(clip_count=3))
    merged = merge_candidates(windows, [], 3)
    assert len(merged) == len(windows)
    assert all(getattr(item, "score", None) is not None for item in merged)


def test_merge_candidates_prefers_ai_picks_without_duplicating_windows():
    deterministic = detect_highlights(TALK, duration=40.0, options=HighlightOptions(clip_count=3))
    ai_choice = deterministic[-1]
    merged = merge_candidates(deterministic, [ai_choice], 5)
    assert merged[0].start == ai_choice.start
    assert len(merged) <= 5


def test_filler_stripping_is_conservative():
    cleaned = strip_filler_words("Um, so uh this is basically fine")
    assert "um" not in cleaned.lower().split()
    assert "uh" not in cleaned.lower().split()
    assert "this is basically fine" in cleaned
    assert strip_filler_words("This is fine") == "This is fine"
