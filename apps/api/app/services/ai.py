"""Provider-independent text generation with structured output validation.

Adapters: local Ollama, any OpenAI-compatible endpoint, and a deterministic
"no model" mode used by features that do not need generative AI. Business code
depends on the ``TextProvider`` protocol only, never on a vendor SDK.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.errors import ProviderUnavailable

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


class AIError(ProviderUnavailable):
    pass


@dataclass
class Completion:
    text: str
    provider: str
    model: str | None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TextProvider(Protocol):
    id: str
    model: str | None

    def available(self) -> tuple[bool, str | None]: ...

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> Completion: ...


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    attempts = max(1, settings.ai_max_retries + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=settings.ai_timeout_seconds) as client:
                response = client.post(url, json=payload, headers=headers or {})
            if response.status_code in {429, 500, 502, 503, 504}:
                last_error = AIError(
                    f"Provider temporarily unavailable (HTTP {response.status_code})",
                    code="ai_upstream_unavailable",
                )
                time.sleep(min(2 ** attempt, 8))
                continue
            if response.status_code >= 400:
                detail = response.text[:400]
                raise AIError(
                    f"Provider rejected the request (HTTP {response.status_code}): {detail}",
                    code="ai_request_rejected",
                )
            return response.json()
        except httpx.TimeoutException as exc:
            last_error = AIError(
                f"Provider timed out after {settings.ai_timeout_seconds}s",
                code="ai_timeout",
            )
            time.sleep(min(2 ** attempt, 8))
            _ = exc
        except httpx.ConnectError as exc:
            raise AIError(
                f"Cannot reach the model endpoint at {url.split('/api/')[0]}: {exc}. "
                "Verify the service is running and CF_AI_BASE_URL / CF_OLLAMA_BASE_URL are correct.",
                code="ai_unreachable",
            ) from exc
        except httpx.HTTPError as exc:
            last_error = AIError(f"Provider request failed: {exc}", code="ai_request_failed")
            break
    raise last_error or AIError("Provider request failed", code="ai_request_failed")


class OllamaProvider:
    id = "ollama"

    def __init__(self) -> None:
        self.model = settings.ai_model or settings.ollama_model
        self.base_url = (settings.ai_base_url or settings.ollama_base_url).rstrip("/")

    def available(self) -> tuple[bool, str | None]:
        if not self.base_url:
            return False, "CF_OLLAMA_BASE_URL is not set"
        return True, None

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "temperature": settings.ai_temperature if temperature is None else temperature,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"
        started = time.monotonic()
        data = _post_json(f"{self.base_url}/api/chat", payload)
        elapsed = int((time.monotonic() - started) * 1000)
        message = (data.get("message") or {}).get("content", "")
        if not message:
            raise AIError("Ollama returned an empty response", code="ai_empty_response")
        return Completion(
            text=message,
            provider=self.id,
            model=self.model,
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
            latency_ms=elapsed,
            raw={"done_reason": data.get("done_reason")},
        )


class OpenAICompatibleProvider:
    id = "openai_compatible"

    def __init__(self) -> None:
        self.model = settings.ai_model or "gpt-4o-mini"
        self.base_url = (settings.ai_base_url or "https://api.openai.com/v1").rstrip("/")

    def available(self) -> tuple[bool, str | None]:
        if not settings.ai_api_key:
            return False, "CF_AI_API_KEY is not set"
        return True, None

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1200,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": settings.ai_temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        data = _post_json(
            f"{self.base_url}/chat/completions",
            payload,
            headers={
                "Authorization": f"Bearer {settings.ai_api_key}",
                "Content-Type": "application/json",
            },
        )
        elapsed = int((time.monotonic() - started) * 1000)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("Provider returned an unexpected payload", code="ai_bad_payload") from exc
        usage = data.get("usage") or {}
        return Completion(
            text=text or "",
            provider=self.id,
            model=self.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=elapsed,
            raw={"finish_reason": (data["choices"][0] or {}).get("finish_reason")},
        )


_PROVIDERS: dict[str, type] = {
    "ollama": OllamaProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def provider_status() -> dict[str, Any]:
    configured = settings.ai_configured()
    if settings.ai_provider == "none":
        return {
            "id": "none",
            "label": "Deterministic only (no language model)",
            "configured": False,
            "detail": "Highlight ranking, captions and metadata use local algorithms. "
            "Set CF_AI_PROVIDER=ollama to enable semantic analysis and AI content writer output.",
        }
    provider = _PROVIDERS.get(settings.ai_provider)
    if provider is None:
        return {
            "id": settings.ai_provider,
            "label": settings.ai_provider,
            "configured": False,
            "detail": f"Unknown provider '{settings.ai_provider}'",
        }
    instance = provider()
    ok, reason = instance.available()
    label = "Ollama (local)" if settings.ai_provider == "ollama" else "OpenAI-compatible"
    return {
        "id": settings.ai_provider,
        "label": label,
        "configured": ok and configured,
        "detail": reason or f"Model: {instance.model} at {instance.base_url}",
    }


def get_text_provider(preferred: str | None = None) -> TextProvider:
    provider_id = preferred or settings.ai_provider
    if provider_id in {None, "", "none"}:
        raise AIError(
            "No language model is configured. Deterministic mode remains available for "
            "clip ranking and templates; configure CF_AI_PROVIDER to use generative features.",
            code="ai_not_configured",
        )
    provider_cls = _PROVIDERS.get(provider_id)
    if provider_cls is None:
        raise AIError(f"Unsupported AI provider: {provider_id}", code="ai_unknown_provider")
    provider = provider_cls()
    ok, reason = provider.available()
    if not ok:
        raise AIError(reason or "Provider not configured", code="ai_not_configured")
    return provider


def test_provider(provider_id: str | None = None) -> dict[str, Any]:
    """Connection test used by the settings screen."""
    try:
        provider = get_text_provider(provider_id)
    except AIError as exc:
        return {"ok": False, "provider": provider_id or settings.ai_provider, "message": exc.message}
    try:
        result = provider.complete(
            system="You are a connectivity probe. Reply with the single word: pong",
            prompt="ping",
            temperature=0.0,
            max_tokens=8,
        )
        return {
            "ok": True,
            "provider": result.provider,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "message": (result.text or "").strip()[:120],
        }
    except AIError as exc:
        return {"ok": False, "provider": provider.id, "message": exc.message, "code": exc.code}


def structured_completion(
    provider: TextProvider,
    *,
    system: str,
    prompt: str,
    schema: type[TModel],
    max_tokens: int = 1500,
) -> tuple[TModel | None, Completion | None, str | None]:
    """Ask for JSON matching ``schema``; return (model, completion, error).

    Invalid or non-conforming output is rejected — never coerced into a
    fabricated result. One corrective retry is attempted with the validation
    error appended, which materially improves small-model reliability.
    """
    schema_json = json.dumps(schema.model_json_schema(), separators=(",", ":"))[:4000]
    instruction = (
        f"{system}\n\nRespond with a single JSON object that validates against this JSON Schema. "
        f"No prose, no markdown fences.\nJSON Schema: {schema_json}"
    )
    last_error: str | None = None
    completion: Completion | None = None
    for attempt in range(2):
        try:
            completion = provider.complete(
                system=instruction,
                prompt=prompt if attempt == 0 else f"{prompt}\n\nPrevious attempt failed validation: {last_error}",
                json_mode=True,
                max_tokens=max_tokens,
            )
        except AIError as exc:
            return None, None, exc.message
        cleaned = _extract_json(completion.text)
        if cleaned is None:
            last_error = "Response was not valid JSON"
            continue
        try:
            parsed = schema.model_validate(cleaned)
        except ValidationError as exc:
            last_error = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:5]
            )
            continue
        return parsed, completion, None
    logger.warning("Structured completion rejected: %s", last_error)
    return None, completion, last_error or "Model output failed validation"


def _extract_json(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
