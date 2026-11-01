"""AI Content Studio.

Generative output is optional. When no language model is configured the studio
still produces *extractive* drafts (verbatim transcript material plus structural
templates), clearly labelled as deterministic so nobody mistakes them for model
output. Provider failures are returned as errors, never as fabricated copy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from pydantic import BaseModel, Field

from app.errors import ProviderUnavailable
from app.models import ContentKind
from app.services.ai import AIError, TextProvider, structured_completion
from app.services.highlights import STOPWORDS, _tokens

TONE_GUIDE = {
    "punchy": "short, high-energy sentences; front-load the payoff",
    "professional": "clear, credible, no hype",
    "casual": "conversational, first person, light",
    "educational": "teach the idea step by step",
    "bold": "direct and opinionated, but never fabricated",
}


class GeneratedItem(BaseModel):
    kind: ContentKind
    title: str | None = None
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


class AIContentItem(BaseModel):
    kind: ContentKind
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=12000)


class AIContentList(BaseModel):
    items: list[AIContentItem] = Field(default_factory=list, max_length=40)


@dataclass
class GenerationOutcome:
    items: list[GeneratedItem]
    provider: str
    model: str | None
    tokens: int = 0
    warnings: list[str] = field(default_factory=list)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def keywords(text: str, limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for token in _tokens(text):
        if token in STOPWORDS or len(token) < 4:
            continue
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def hashtags_for(text: str, platform: str | None = None, limit: int = 8) -> list[str]:
    base = keywords(text, limit=limit)
    tags = [f"#{word.replace('-', '')}" for word in base]
    if platform == "youtube":
        tags.append("#Shorts")
    elif platform == "tiktok":
        tags.append("#fyp")
    return tags[:limit]


def _extractive_summary(text: str, sentences_wanted: int = 3) -> str:
    parts = _sentences(text)
    if not parts:
        return text.strip()
    return " ".join(parts[:sentences_wanted])


# --------------------------------------------------------------------------- deterministic
def deterministic_clip_content(
    *,
    excerpt: str,
    title: str,
    platform: str | None,
    tone: str,
    kinds: Sequence[ContentKind],
    count: int = 3,
) -> GenerationOutcome:
    items: list[GeneratedItem] = []
    sentences = _sentences(excerpt)
    first = sentences[0] if sentences else excerpt[:160]
    summary = _extractive_summary(excerpt, 3)
    tags = hashtags_for(excerpt, platform)
    meta = {"mode": "extractive_template", "source": "transcript"}

    for kind in kinds:
        if kind in {ContentKind.TITLE, ContentKind.YOUTUBE_TITLE}:
            for index in range(min(count, 3)):
                if index < len(sentences):
                    body = sentences[index][:95].strip(" ,.;")
                else:
                    body = title[:95]
                items.append(GeneratedItem(kind=kind, body=body, metadata=meta))
        elif kind == ContentKind.HOOK:
            for index in range(min(count, 2)):
                source = sentences[index] if index < len(sentences) else first
                items.append(GeneratedItem(kind=kind, body=source.strip(), metadata=meta))
        elif kind == ContentKind.DESCRIPTION:
            items.append(
                GeneratedItem(
                    kind=kind,
                    body=f"{summary}\n\n{title}\n\n{' '.join(tags)}",
                    metadata=meta,
                )
            )
        elif kind == ContentKind.CAPTION:
            body = summary
            if platform in {"tiktok", "instagram", "x"}:
                body = f"{first}\n\n{' '.join(tags[:5])}"
            items.append(GeneratedItem(kind=kind, body=body[:2200], metadata=meta))
        elif kind == ContentKind.HASHTAGS:
            items.append(GeneratedItem(kind=kind, body=" ".join(tags), metadata=meta))
        elif kind == ContentKind.CTA:
            items.append(
                GeneratedItem(
                    kind=kind,
                    body="Template (not model generated): “If this helped, follow for more breakdowns like this.”",
                    metadata={**meta, "template": True},
                )
            )
        elif kind == ContentKind.THUMBNAIL_TEXT:
            words = keywords(excerpt, limit=4)
            body = " ".join(word.upper() for word in words) or title.upper()[:40]
            items.append(GeneratedItem(kind=kind, body=body, metadata=meta))
        elif kind == ContentKind.PINNED_COMMENT:
            items.append(
                GeneratedItem(
                    kind=kind,
                    body=f"Template (not model generated): “Full breakdown starts at the {sentences[0][:60] if sentences else 'beginning'}…”",
                    metadata={**meta, "template": True},
                )
            )
        elif kind == ContentKind.SUMMARY:
            items.append(GeneratedItem(kind=kind, body=summary, metadata=meta))
        else:
            items.append(GeneratedItem(kind=kind, body=summary, metadata=meta))
    return GenerationOutcome(items=items, provider="deterministic", model=None)


def deterministic_project_content(
    *,
    segments: Sequence[dict[str, Any]],
    project_title: str,
    kinds: Sequence[ContentKind],
) -> GenerationOutcome:
    items: list[GeneratedItem] = []
    full_text = " ".join(str(seg.get("text", "")) for seg in segments)
    summary = _extractive_summary(full_text, 5)
    tags = hashtags_for(full_text, limit=10)
    meta = {"mode": "extractive_template", "source": "transcript"}

    for kind in kinds:
        if kind in {ContentKind.TITLE, ContentKind.YOUTUBE_TITLE}:
            items.append(GeneratedItem(kind=kind, body=_extractive_summary(full_text, 1)[:95].strip(" ,.;"), metadata=meta))
        elif kind == ContentKind.DESCRIPTION:
            items.append(
                GeneratedItem(kind=kind, body=f"{summary}\n\nChapters and timestamps are generated from the transcript.", metadata=meta)
            )
        elif kind == ContentKind.CHAPTERS:
            items.append(GeneratedItem(kind=kind, body=_chapters_from_segments(segments), metadata={**meta, "grounded": True}))
        elif kind == ContentKind.SUMMARY:
            items.append(GeneratedItem(kind=kind, body=summary, metadata=meta))
        elif kind == ContentKind.BLOG:
            paragraphs = [summary]
            for sentence in _sentences(full_text)[:12]:
                paragraphs.append(sentence)
            items.append(GeneratedItem(kind=kind, body="\n\n".join(paragraphs), metadata=meta))
        elif kind in {ContentKind.LINKEDIN, ContentKind.X_POST, ContentKind.NEWSLETTER}:
            items.append(GeneratedItem(kind=kind, body=summary[:1200], metadata=meta))
        elif kind == ContentKind.HASHTAGS:
            items.append(GeneratedItem(kind=kind, body=" ".join(tags), metadata=meta))
        else:
            items.append(GeneratedItem(kind=kind, body=summary, metadata=meta))
    return GenerationOutcome(items=items, provider="deterministic", model=None)


def _chapters_from_segments(segments: Sequence[dict[str, Any]], every_seconds: float = 90.0) -> str:
    """Chapters anchored to real transcript timestamps (never invented)."""
    if not segments:
        return "No chapters available (no transcript)."
    lines: list[str] = []
    last_at = -every_seconds
    for seg in segments:
        start = float(seg.get("start_seconds", 0.0))
        if start - last_at < every_seconds and lines:
            continue
        if start <= 0.5 or lines:
            text = str(seg.get("text", "")).strip()
            words = text.split()
            label = " ".join(words[:8]).strip(" ,.") or "Section"
            minutes, seconds = divmod(int(start), 60)
            hours, minutes = divmod(minutes, 60)
            stamp = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
            lines.append(f"{stamp} {label[:70]}")
            last_at = start
        if len(lines) >= 12:
            break
    return "\n".join(lines) or "No chapters available."


# --------------------------------------------------------------------------- AI
def generate_with_ai(
    provider: TextProvider,
    *,
    kinds: Sequence[ContentKind],
    transcript_excerpt: str,
    context_title: str,
    platform: str | None,
    tone: str,
    count: int = 3,
    is_clip: bool = True,
) -> GenerationOutcome:
    kind_list = ", ".join(kind.value for kind in kinds)
    system = (
        "You write social media copy for short-form video. Ground every claim in the supplied "
        "transcript excerpt: never invent facts, numbers, quotes or people. Follow the requested tone. "
        "Return strict JSON."
    )
    prompt = (
        f"Title: {context_title}\nPlatform: {platform or 'generic'}\nTone: {tone} ({TONE_GUIDE.get(tone, '')})\n"
        f"Task: produce {count} variations where useful for each of these content kinds: {kind_list}.\n"
        f"{'Clip' if is_clip else 'Long-form video'} transcript excerpt:\n\"\"\"{transcript_excerpt[:6000]}\"\"\"\n\n"
        'Return {"items": [{"kind": "<one of ' + kind_list + '>", "title": string|null, '
        '"body": string}]}. Hashtags go in a single hashtags item as space separated tags.'
    )
    schema = AIContentList
    parsed, completion, error = structured_completion(
        provider, system=system, prompt=prompt, schema=schema, max_tokens=2000
    )
    if parsed is None:
        raise ProviderUnavailable(
            f"The configured model could not produce valid content: {error}",
            code="content_generation_failed",
        )
    allowed = set(kinds)
    items = [
        GeneratedItem(
            kind=item.kind,
            title=item.title,
            body=item.body.strip(),
            metadata={"mode": "model", "platform": platform, "tone": tone},
        )
        for item in parsed.items
        if item.kind in allowed
    ]
    warnings: list[str] = []
    if not items:
        warnings.append("Model returned no items matching the requested content kinds.")
    return GenerationOutcome(
        items=items,
        provider=getattr(provider, "id", "unknown"),
        model=getattr(provider, "model", None),
        tokens=completion.total_tokens if completion else 0,
        warnings=warnings,
    )


def provider_or_none(preferred: str | None) -> TextProvider | None:
    from app.config import settings
    from app.services.ai import get_text_provider

    if (preferred or settings.ai_provider) in {"none", ""}:
        return None
    try:
        return get_text_provider(preferred)
    except AIError:
        return None
