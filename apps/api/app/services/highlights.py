"""Highlight detection and candidate ranking.

Two modes, clearly labelled in the data:

* ``deterministic``  — pure local algorithm over transcript structure (pauses,
  sentence boundaries, speech density, lexical salience). No semantic "AI
  understanding" is claimed.
* ``ai_assisted``    — an optional language model proposes windows; every
  proposal is validated against the real transcript, snapped to real timestamps
  and re-scored locally. The excerpt is always read from the transcript, never
  from the model, so invented text cannot leak into a clip.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from pydantic import BaseModel, Field

from app.services.ai import AIError, TextProvider, structured_completion

FILLER_WORDS = {
    "um", "uh", "erm", "ah", "eh", "hmm", "like", "so", "you know", "i mean",
    "basically", "actually", "literally", "right", "okay", "ok",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "its", "our", "their", "is", "are", "was", "were", "be", "been", "being", "am", "do",
    "does", "did", "doing", "have", "has", "had", "having", "will", "would", "shall", "should",
    "can", "could", "may", "might", "must", "of", "to", "in", "on", "at", "by", "for", "with",
    "about", "against", "between", "into", "through", "during", "before", "after", "above",
    "below", "from", "up", "down", "out", "off", "over", "under", "again", "further", "once",
    "here", "there", "when", "where", "why", "how", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own", "same", "too", "very",
    "just", "as", "also", "get", "got", "go", "going", "like", "know", "think", "want", "well",
    "thing", "things", "really", "because", "what", "which", "who", "whom", "one", "two",
}

CURIOSITY_MARKERS = {
    "why", "how", "secret", "mistake", "never", "nobody", "everyone", "truth", "actually",
    "biggest", "best", "worst", "mistake", "trick", "framework", "system", "rule", "problem",
    "wrong", "hidden", "nobody tells you", "most people", "here's", "listen", "imagine",
}
HOOK_STARTERS = ("how", "why", "what", "when", "stop", "never", "most", "everyone", "nobody", "here", "the biggest")

CATEGORY_RULES: list[tuple[str, set[str]]] = [
    ("tutorial", {"how", "step", "tutorial", "guide", "setup", "install", "build", "learn", "example"}),
    ("story", {"story", "happened", "years", "remember", "when i", "once", "then"}),
    ("motivation", {"mindset", "discipline", "success", "fail", "grow", "habit", "goal", "focus"}),
    ("opinion", {"think", "believe", "opinion", "wrong", "agree", "actually"}),
    ("interview", {"asked", "question", "answer", "said", "told"}),
    ("product", {"product", "launch", "customer", "feature", "price", "offer"}),
]


class AICandidate(BaseModel):
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    title: str = Field(min_length=3, max_length=120)
    hook: str = Field(min_length=3, max_length=200)
    category: str = Field(default="general", max_length=40)
    rationale: str = Field(default="", max_length=400)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class AICandidateList(BaseModel):
    candidates: list[AICandidate] = Field(default_factory=list, max_length=30)


@dataclass
class CandidateWindow:
    start: float
    end: float
    excerpt: str
    title: str
    hook: str
    category: str
    score: float
    breakdown: dict[str, float]
    explanation: str
    seg_indices: list[int] = field(default_factory=list)
    word_count: int = 0
    speech_seconds: float = 0.0
    algorithm: str = "deterministic"
    model: str | None = None
    validation_status: str = "valid"
    validation_notes: str | None = None
    source: str = "deterministic"


@dataclass
class HighlightOptions:
    clip_count: int = 6
    min_duration: float = 18.0
    max_duration: float = 90.0
    target_duration: float = 42.0
    pause_threshold: float = 0.6
    max_overlap_ratio: float = 0.35


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9']+", text.lower())


def _sentence_ends(text: str) -> bool:
    return bool(re.search(r"[.!?][\"')\]]?\s*$", text.strip()))


def _salient_terms(segments: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Inverse-frequency weighting of content words across the transcript."""
    total_docs = max(1, len(segments))
    doc_freq: dict[str, int] = {}
    for seg in segments:
        seen = {tok for tok in _tokens(seg.get("text", "")) if tok not in STOPWORDS and len(tok) > 2}
        for token in seen:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    return {term: math.log(1 + total_docs / (1 + freq)) for term, freq in doc_freq.items()}


def _boundaries(segments: Sequence[dict[str, Any]], pause_threshold: float) -> list[int]:
    """Indexes of segments that start a new speech unit."""
    starts: list[int] = []
    for index, seg in enumerate(segments):
        if index == 0:
            starts.append(index)
            continue
        previous = segments[index - 1]
        gap = float(seg.get("start_seconds", 0.0)) - float(previous.get("end_seconds", 0.0))
        if gap >= pause_threshold or _sentence_ends(previous.get("text", "")):
            starts.append(index)
    return starts


def _build_windows(
    segments: Sequence[dict[str, Any]], options: HighlightOptions, duration: float
) -> list[tuple[int, int]]:
    boundaries = _boundaries(segments, options.pause_threshold)
    windows: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for b_index, start_idx in enumerate(boundaries):
        start_time = float(segments[start_idx]["start_seconds"])
        running = 0.0
        for end_idx in range(start_idx, len(segments)):
            seg = segments[end_idx]
            running = float(seg["end_seconds"]) - start_time
            if running < options.min_duration:
                continue
            if running > options.max_duration:
                break
            is_boundary_end = (end_idx + 1) in boundaries or end_idx == len(segments) - 1
            if not is_boundary_end:
                continue
            if running < options.min_duration * 0.9:
                continue
            key = (start_idx, end_idx)
            if key not in seen:
                seen.add(key)
                windows.append(key)
            # Only take the best-fitting window per start plus one shorter option
            if len([w for w in windows if w[0] == start_idx]) >= 4:
                break
        if len(windows) > 400:
            break
        _ = b_index
    if not windows:
        # Fall back to fixed windows so short transcripts still produce candidates.
        step = max(options.min_duration, options.target_duration * 0.75)
        cursor = 0.0
        while cursor + options.min_duration <= max(duration, options.min_duration):
            end = min(cursor + options.target_duration, duration)
            idx = [
                i
                for i, seg in enumerate(segments)
                if float(seg["end_seconds"]) > cursor and float(seg["start_seconds"]) < end
            ]
            if idx:
                windows.append((idx[0], idx[-1]))
            cursor += step
    return windows


def _category_for(text: str) -> str:
    lowered = text.lower()
    scores = {
        name: sum(1 for word in words if word in lowered) for name, words in CATEGORY_RULES
    }
    best = max(scores.items(), key=lambda item: item[1])
    return best[0] if best[1] > 0 else "general"


def _deterministic_title(text: str, limit: int = 90) -> str:
    """Title built only from words that exist in the transcript excerpt."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned)[0] if cleaned else ""
    candidate = first_sentence or cleaned
    if len(candidate) > limit:
        candidate = candidate[: limit - 1].rsplit(" ", 1)[0] + "…"
    return candidate.strip(" ,.;") or "Untitled highlight"


def _hook_for(text: str, limit: int = 140) -> str:
    tokens = text.split()
    hook = " ".join(tokens[:18])
    if len(tokens) > 18:
        hook += "…"
    return hook.strip() or text[:limit]


def _score_window(
    segments: Sequence[dict[str, Any]],
    window: tuple[int, int],
    salient: dict[str, float],
    options: HighlightOptions,
) -> tuple[float, dict[str, float], str, int, float]:
    start_idx, end_idx = window
    slice_ = segments[start_idx : end_idx + 1]
    text = " ".join(seg.get("text", "") for seg in slice_).strip()
    start = float(slice_[0]["start_seconds"])
    end = float(slice_[-1]["end_seconds"])
    length = max(0.001, end - start)

    tokens = [tok for tok in _tokens(text) if tok not in STOPWORDS]
    word_count = len(re.findall(r"\S+", text))
    speech_seconds = sum(
        max(0.0, float(seg["end_seconds"]) - float(seg["start_seconds"])) for seg in slice_
    )
    speech_ratio = min(1.0, speech_seconds / length) if length else 0.0
    density = word_count / max(speech_seconds, 0.001)

    # 1. Length fit (peak at the target duration)
    ideal = options.target_duration
    length_fit = max(0.0, 1.0 - abs(length - ideal) / max(ideal, options.max_duration - options.min_duration))

    # 2. Boundary quality
    boundary = 0.0
    if _sentence_ends(segments[start_idx].get("text", "")) is False and start_idx == 0:
        boundary += 0.4
    if _sentence_ends(segments[end_idx].get("text", "")):
        boundary += 0.6
    if end_idx == len(segments) - 1:
        boundary += 0.2
    boundary = min(1.0, boundary)

    # 3. Salience: sum of inverse-document-frequency weights normalised by length
    salience_raw = sum(salient.get(tok, 0.0) for tok in tokens) / max(1, len(tokens))
    salience = min(1.0, salience_raw / 3.0)

    # 4. Hook strength in the first two sentences
    opening = " ".join(" ".join(seg.get("text", "") for seg in slice_[:2]).split()[:40]).lower()
    hook_hits = sum(1 for marker in CURIOSITY_MARKERS if marker in opening)
    hook_bonus = 0.25 if opening.startswith(HOOK_STARTERS) else 0.0
    question_bonus = 0.2 if "?" in opening else 0.0
    number_bonus = 0.15 if re.search(r"\b\d+\b", opening) else 0.0
    hook_score = min(1.0, hook_hits * 0.22 + hook_bonus + question_bonus + number_bonus)

    # 5. Speech density (words/sec): reward sustained talk, punish dead air
    density_score = min(1.0, max(0.0, (density - 1.2) / 2.0)) * speech_ratio

    # 6. Topic cohesion: repeated content words inside the window
    unique = len(set(tokens))
    cohesion = min(1.0, (unique / max(1, len(tokens))) * 1.35)

    # 7. Penalties
    filler_penalty = 0.0
    first_words = " ".join(slice_[0].get("text", "").split()[:3]).lower()
    if any(first_words.startswith(filler) for filler in FILLER_WORDS):
        filler_penalty += 0.12
    if word_count < 30:
        filler_penalty += 0.1
    if not _sentence_ends(slice_[0].get("text", "")) and start_idx != 0:
        filler_penalty += 0.05

    weights = {
        "length_fit": 0.18,
        "boundary": 0.18,
        "salience": 0.20,
        "hook": 0.22,
        "density": 0.12,
        "cohesion": 0.10,
    }
    components = {
        "length_fit": round(length_fit, 4),
        "boundary": round(boundary, 4),
        "salience": round(salience, 4),
        "hook": round(hook_score, 4),
        "density": round(density_score, 4),
        "cohesion": round(cohesion, 4),
    }
    raw = sum(components[name] * weight for name, weight in weights.items())
    score = max(0.0, min(1.0, raw + 0.0 - filler_penalty))

    explanation_bits = [
        f"hook strength {components['hook']:.2f}",
        f"lexical salience {components['salience']:.2f}",
        f"boundary quality {components['boundary']:.2f}",
        f"length fit {components['length_fit']:.2f} for {length:.0f}s",
        f"speech density {density:.2f} words/s",
    ]
    if filler_penalty:
        explanation_bits.append(f"penalty -{filler_penalty:.2f} (weak opening)")
    explanation = "Scored on " + ", ".join(explanation_bits) + "."
    return score, components, explanation, word_count, speech_seconds


def _overlap_ratio(a: tuple[float, float], b: tuple[float, float]) -> float:
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    if end <= start:
        return 0.0
    return (end - start) / max(0.001, min(a[1] - a[0], b[1] - b[0]))


def detect_highlights(
    segments: Sequence[dict[str, Any]],
    *,
    duration: float,
    options: HighlightOptions | None = None,
) -> list[CandidateWindow]:
    options = options or HighlightOptions()
    if not segments:
        return []
    salient = _salient_terms(segments)
    windows = _build_windows(segments, options, duration)
    scored: list[CandidateWindow] = []
    for window in windows:
        score, breakdown, explanation, word_count, speech_seconds = _score_window(
            segments, window, salient, options
        )
        start_idx, end_idx = window
        excerpt = " ".join(seg.get("text", "") for seg in segments[start_idx : end_idx + 1]).strip()
        start = float(segments[start_idx]["start_seconds"])
        end = float(segments[end_idx]["end_seconds"])
        if end - start < options.min_duration * 0.85:
            continue
        scored.append(
            CandidateWindow(
                start=round(start, 3),
                end=round(min(end, duration or end), 3),
                excerpt=excerpt,
                title=_deterministic_title(excerpt),
                hook=_hook_for(excerpt),
                category=_category_for(excerpt),
                score=round(score, 4),
                breakdown=breakdown,
                explanation=explanation,
                seg_indices=list(range(start_idx, end_idx + 1)),
                word_count=word_count,
                speech_seconds=round(speech_seconds, 3),
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    selected: list[CandidateWindow] = []
    for candidate in scored:
        if any(
            _overlap_ratio((candidate.start, candidate.end), (chosen.start, chosen.end))
            > options.max_overlap_ratio
            for chosen in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= options.clip_count:
            break
    return selected


# --------------------------------------------------------------------------- AI assisted
def compact_transcript_for_ai(
    segments: Sequence[dict[str, Any]], *, max_chars: int = 12000
) -> tuple[str, list[dict[str, Any]]]:
    """Send only what the model needs: sentence-level text plus timestamps.

    Word-level timings, probabilities and speaker tags are stripped to keep
    prompts small and to avoid shipping unnecessary personal data to a provider.
    """
    lines: list[str] = []
    used: list[dict[str, Any]] = []
    total = 0
    for seg in segments:
        text = re.sub(r"\s+", " ", str(seg.get("text", ""))).strip()
        if not text:
            continue
        line = f"[{float(seg['start_seconds']):.1f}-{float(seg['end_seconds']):.1f}] {text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        used.append(seg)
        total += len(line)
    return "\n".join(lines), used


def ai_candidates(
    provider: TextProvider,
    segments: Sequence[dict[str, Any]],
    *,
    duration: float,
    options: HighlightOptions,
    language: str | None = None,
) -> tuple[list[CandidateWindow], dict[str, Any]]:
    """Ask the model for windows, then validate/snap/score them locally."""
    transcript_text, used = compact_transcript_for_ai(segments)
    if not transcript_text:
        return [], {"error": "Transcript was empty"}
    system = (
        "You are a short-form video editor. Select self-contained moments from the transcript "
        "that work as standalone vertical clips. Only use timestamps that appear in the input. "
        "Never invent quotes, facts or events. Return strict JSON."
    )
    prompt = (
        f"Transcript (timestamped, language={language or 'auto'}):\n{transcript_text}\n\n"
        f"Choose up to {options.clip_count} clips. Each clip must be between "
        f"{options.min_duration:.0f} and {options.max_duration:.0f} seconds and start/end on "
        "a sentence boundary. `title` must be a short label using words from that segment. "
        "`hook` is the first spoken line of the clip. `rationale` explains why the clip stands alone. "
        'Return {"candidates": [{"start_seconds": number, "end_seconds": number, "title": string, '
        '"hook": string, "category": string, "rationale": string, "confidence": number}]}'
    )
    parsed, completion, error = structured_completion(
        provider, system=system, prompt=prompt, schema=AICandidateList, max_tokens=1800
    )
    meta = {
        "provider": getattr(provider, "id", "unknown"),
        "model": getattr(provider, "model", None),
        "tokens": completion.total_tokens if completion else 0,
        "latency_ms": completion.latency_ms if completion else 0,
        "error": error,
    }
    if parsed is None:
        return [], meta

    starts = sorted({float(seg["start_seconds"]) for seg in used})
    ends = sorted({float(seg["end_seconds"]) for seg in used})
    validated: list[CandidateWindow] = []
    rejected: list[str] = []

    for index, item in enumerate(parsed.candidates):
        # Snap to the closest real transcript boundary
        start = min(starts, key=lambda value: abs(value - item.start_seconds))
        end = min(ends, key=lambda value: abs(value - item.end_seconds))
        if end <= start:
            rejected.append(f"candidate {index + 1}: end before start")
            continue
        if abs(start - item.start_seconds) > 3.0 or abs(end - item.end_seconds) > 3.0:
            rejected.append(f"candidate {index + 1}: timestamps not grounded in transcript")
            continue
        length = end - start
        if length < options.min_duration * 0.85 or length > options.max_duration * 1.1:
            rejected.append(f"candidate {index + 1}: duration {length:.0f}s outside limits")
            continue
        if duration and end > duration + 0.5:
            rejected.append(f"candidate {index + 1}: exceeds media duration")
            continue
        included = [
            seg
            for seg in used
            if float(seg["end_seconds"]) > start and float(seg["start_seconds"]) < end
        ]
        if not included:
            rejected.append(f"candidate {index + 1}: no transcript content in range")
            continue
        excerpt = " ".join(seg.get("text", "") for seg in included).strip()
        window = (segments.index(included[0]), segments.index(included[-1]))
        det_score, breakdown, explanation, word_count, speech_seconds = _score_window(
            segments, window, _salient_terms(segments), options
        )
        blended = round(0.55 * item.confidence + 0.45 * det_score, 4)
        breakdown["model_confidence"] = round(item.confidence, 4)
        breakdown["deterministic"] = round(det_score, 4)
        validated.append(
            CandidateWindow(
                start=round(start, 3),
                end=round(end, 3),
                excerpt=excerpt,
                title=item.title.strip()[:290],
                hook=(item.hook or _hook_for(excerpt)).strip()[:390],
                category=(item.category or _category_for(excerpt))[:58],
                score=blended,
                breakdown=breakdown,
                explanation=(
                    f"Model rationale: {item.rationale.strip() or 'n/a'}. Local score {det_score:.2f} "
                    f"({explanation})"
                ),
                seg_indices=[segments.index(s) for s in included],
                word_count=word_count,
                speech_seconds=round(speech_seconds, 3),
                algorithm="ai_assisted",
                model=getattr(provider, "model", None),
                source="ai_assisted",
                validation_status="valid",
            )
        )

    if rejected:
        meta["rejected"] = rejected
    validated.sort(key=lambda candidate: candidate.score, reverse=True)
    return validated[: options.clip_count], meta


def merge_candidates(
    deterministic: list[CandidateWindow], ai: list[CandidateWindow], limit: int
) -> list[CandidateWindow]:
    """Keep AI picks first, then fill remaining slots with non-overlapping local picks."""
    merged: list[CandidateWindow] = []
    for candidate in ai + deterministic:
        if any(
            _overlap_ratio((candidate.start, candidate.end), (chosen.start, chosen.end)) > 0.35
            for chosen in merged
        ):
            continue
        merged.append(candidate)
        if len(merged) >= limit:
            break
    return merged


def compute_silence_removal(
    silences: Iterable[tuple[float, float]],
    *,
    clip_start: float,
    clip_end: float,
    min_silence: float = 0.6,
    keep_padding: float = 0.08,
) -> list[tuple[float, float]]:
    """Segments of the clip that should be kept after silence stripping."""
    kept: list[tuple[float, float]] = []
    cursor = clip_start
    for start, end in sorted(silences):
        if end <= clip_start or start >= clip_end:
            continue
        start = max(start, clip_start)
        end = min(end, clip_end)
        if end - start < min_silence:
            continue
        cut_start = start + keep_padding
        cut_end = end - keep_padding
        if cut_end <= cut_start:
            continue
        if cut_start > cursor:
            kept.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < clip_end:
        kept.append((cursor, clip_end))
    return [(round(a, 3), round(b, 3)) for a, b in kept if b - a > 0.15]


def strip_filler_words(text: str) -> str:
    pattern = re.compile(r"\b(um|uh|erm|ah|hmm)\b[ ,]*", re.IGNORECASE)
    cleaned = pattern.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def remove_fillers_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in segments:
        copy = dict(seg)
        copy["text"] = strip_filler_words(str(seg.get("text", "")))
        out.append(copy)
    return out


_ = (AIError,)
