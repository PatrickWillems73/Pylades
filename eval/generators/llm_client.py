"""Minimale Anthropic-client voor het eval-harnas.

Hergebruikt hetzelfde directe HTTP-contract als de proxy (x-api-key +
anthropic-version), maar synchroon — datageneratie is een offline batch, geen
request-pad. De API-sleutel komt uit dezelfde `settings.anthropic_api_key`.

`discover_model()` bevraagt de modellen-endpoint en kiest het nieuwste
Opus-model, zodat we geen mogelijk-verouderde model-id hardcoden.
"""

from __future__ import annotations

import logging

import httpx

from shared.config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.anthropic.com/v1"
_ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT = 120.0


class AnthropicError(RuntimeError):
    """Faal expliciet zodat de generator niet stilletjes lege data oplevert."""


def _headers() -> dict[str, str]:
    if not settings.anthropic_api_key:
        raise AnthropicError(
            "Geen anthropic_api_key in .env; datageneratie via de API is niet mogelijk."
        )
    return {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def list_models() -> list[dict]:
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.get(f"{_BASE_URL}/models", headers=_headers())
    if resp.status_code >= 400:
        raise AnthropicError(f"models-endpoint gaf HTTP {resp.status_code}: {resp.text}")
    return list(resp.json().get("data", []))


def discover_model(prefer: str = "opus") -> str:
    """Kies het nieuwste model waarvan de id `prefer` bevat (default: opus)."""
    models = list_models()
    matches = [m for m in models if prefer in str(m.get("id", "")).lower()]
    if not matches:
        available = ", ".join(str(m.get("id")) for m in models)
        raise AnthropicError(f"Geen {prefer!r}-model gevonden. Beschikbaar: {available}")
    matches.sort(key=lambda m: str(m.get("created_at", "")), reverse=True)
    chosen = str(matches[0]["id"])
    logger.info("Datagen-model gekozen: %s", chosen)
    return chosen


def complete(
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 2000,
    temperature: float | None = None,
) -> str:
    """Eén niet-streaming completion; lever de samengevoegde tekst-blokken.

    `temperature` wordt alleen meegestuurd als die expliciet gezet is; nieuwere
    Opus-modellen wijzen de parameter af.
    """
    body: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if temperature is not None:
        body["temperature"] = temperature
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(f"{_BASE_URL}/messages", headers=_headers(), json=body)
    if resp.status_code >= 400:
        raise AnthropicError(f"messages-endpoint gaf HTTP {resp.status_code}: {resp.text}")
    blocks = resp.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    if not text.strip():
        raise AnthropicError("Lege completion ontvangen van Anthropic")
    return text
