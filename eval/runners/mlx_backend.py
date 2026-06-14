"""MLX-backend voor laag 3 (eval-only, TESTPLAN.md §8).

Spiegelt `OllamaBackend` uit [proxy/detection.py](proxy/detection.py), maar
praat met een lokale **OpenAI-compatibele** MLX-server (`mlx_lm.server`) via
`/v1/chat/completions`. Zo kunnen we hetzelfde model (qwen3:1.7b) op twee
backends vergelijken — Ollama (llama.cpp/GGUF) vs MLX (Apple Metal) — zonder de
detectiepijplijn te dupliceren: de backend wordt in `detect_all` geïnjecteerd.

Start de server bijvoorbeeld met:
    uv run --with mlx-lm python -m mlx_lm.server --model mlx-community/Qwen3-1.7B-4bit --port 8081

Twee robuustheidsmaatregelen t.o.v. Ollama, dat met `format="json"` een
JSON-grammar afdwingt:
- `/no_think` onderdrukt Qwen3's redeneer-modus (anders komt er `…`).
- `_extract_json()` / `_salvage_truncated_json()` normaliseren ruwe output;
  bij afkapping (max_tokens) fail-hard met uitleg.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

from proxy.detection import Layer3BackendError
from shared.config import settings


def mlx_server_start_hint(*, host: str, model: str) -> str:
    """Beschrijving hoe de lokale MLX-server te starten (voor foutmeldingen)."""
    port = urlparse(host).port or 8081
    base = host.rstrip("/")
    return (
        f"Start de MLX-server in een aparte terminal:\n"
        f"    uv run --with mlx-lm python -m mlx_lm.server --model {model} --port {port}\n"
        f"Controleer daarna met:\n"
        f"    curl -s {base}/v1/models"
    )


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _extract_json(content: str) -> str:
    """Geef de JSON-objecttekst terug; strip redeneer-tags en omringende ruis."""
    cleaned = _THINK_RE.sub("", content).strip()
    # Markdown code fences (mlx_lm levert soms ```json … ``` zonder grammar).
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def _salvage_truncated_json(text: str) -> str | None:
    """Sluit een afgekapte ``{"entities": [...]}`` JSON netjes af."""
    start = text.find("{")
    if start == -1:
        return None
    body = text[start:].strip()
    last_comma_obj = body.rfind("},")
    if last_comma_obj != -1:
        candidate = f"{body[: last_comma_obj + 1]}\n  ]\n}}"
    else:
        last_obj = body.rfind("}")
        if last_obj == -1:
            return None
        candidate = f"{body[: last_obj + 1]}\n  ]\n}}"
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return candidate


def _normalize_json_response(
    content: str, *, finish_reason: str | None, max_tokens: int
) -> str:
    """Valideer JSON; red afgekapte output of fail-hard met uitleg."""
    extracted = _extract_json(content)
    try:
        json.loads(extracted)
        return extracted
    except json.JSONDecodeError as exc:
        salvaged = _salvage_truncated_json(extracted)
        if salvaged is not None:
            return salvaged
        if finish_reason == "length":
            raise Layer3BackendError(
                "MLX-antwoord afgekapt: max_tokens bereikt voordat de JSON compleet was. "
                f"Het model genereerde meer dan ~{max_tokens} output-tokens (mlx_lm.server "
                "dwingt geen JSON-schema af, anders dan Ollama met format=json). "
                "Vergelijk desnoods met runner pylades_md_llm."
            ) from exc
        raise Layer3BackendError(
            f"MLX-antwoord was geen geldige JSON (finish_reason={finish_reason!r})."
        ) from exc


_MLX_USER_JSON_HINT = (
    "\n\nAntwoord uitsluitend als één geldig JSON-object, zonder markdown of extra tekst."
)


class MLXLayer3Backend:
    """Laag-3-backend tegen een OpenAI-compatibele MLX-server."""

    name = "mlx"
    fail_hard = True

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        *,
        no_think: bool = True,
        timeout: float = 120.0,
        preflight_timeout: float = 5.0,
        max_tokens: int = 2048,
    ) -> None:
        self.host = host or settings.mlx_host
        self.model = model or settings.mlx_model
        self._no_think = no_think
        self._timeout = timeout
        self._preflight_timeout = preflight_timeout
        self._max_tokens = max_tokens

    def unavailable_hint(self) -> str:
        return mlx_server_start_hint(host=self.host, model=self.model)

    def ensure_available(self) -> None:
        """Fail-fast vóór een eval-run als de MLX-server niet luistert."""
        url = f"{self.host.rstrip('/')}/v1/models"
        try:
            with httpx.Client(timeout=self._preflight_timeout) as client:
                resp = client.get(url)
            resp.raise_for_status()
        except Exception as exc:
            raise Layer3BackendError(
                f"MLX-server niet bereikbaar op {self.host}: {type(exc).__name__}: {exc}\n"
                f"{self.unavailable_hint()}"
            ) from exc

    def complete(self, system: str, user: str) -> str:
        user_content = f"{user}{_MLX_USER_JSON_HINT}"
        if self._no_think:
            user_content = f"{user_content}\n\n/no_think"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
        }
        url = f"{self.host.rstrip('/')}/v1/chat/completions"
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, json=body)
        resp.raise_for_status()
        payload = resp.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
        return _normalize_json_response(
            content, finish_reason=finish_reason, max_tokens=self._max_tokens
        )
