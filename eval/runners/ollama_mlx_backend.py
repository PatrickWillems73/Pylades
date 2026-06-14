"""Eval-only Ollama laag-3 met MLX-model (TESTPLAN.md §8).

Spiegelt `OllamaBackend` uit [proxy/detection.py](proxy/detection.py), maar
gebruikt een apart MLX-quantisatiemodel (default `qwen3.5:2b-nvfp4`) dat via
Ollama draait wanneer de server met `OLLAMA_MLX=1` is gestart. Zo krijg je
MLX-snelheid én Ollama's `format="json"` — anders dan `mlx_lm.server`.

Qwen3.5-modellen hebben een denk-modus: zonder `think=False` komt `message.content`
leeg terug (output in `thinking`). Daarom stuurt deze backend expliciet
`think=False` en `/no_think`, plus JSON-normalisatie via `_extract_json`.
"""

from __future__ import annotations

import json

import httpx

from eval.runners.mlx_backend import _extract_json
from proxy.detection import Layer3BackendError, OllamaBackend
from shared.config import settings


def ollama_mlx_start_hint(*, host: str, model: str) -> str:
    """Beschrijving hoe Ollama met MLX-backend te starten."""
    return (
        "Start Ollama met MLX in een aparte terminal:\n"
        "    OLLAMA_MLX=1 ollama serve\n"
        f"Pull het model indien nodig:\n"
        f"    ollama pull {model}\n"
        f"Controleer daarna met:\n"
        f"    ollama run {model} \"test\""
    )


class OllamaMlxEvalBackend(OllamaBackend):
    """Laag-3-backend: Ollama + MLX-modeltag (bv. qwen3.5:2b-nvfp4)."""

    name = "ollama_mlx"
    fail_hard = True

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        *,
        preflight_timeout: float = 5.0,
    ) -> None:
        super().__init__(host=host, model=model or settings.ollama_mlx_model)
        self._preflight_timeout = preflight_timeout

    def unavailable_hint(self) -> str:
        return ollama_mlx_start_hint(host=self.host, model=self.model)

    def ensure_available(self) -> None:
        """Fail-fast vóór een eval-run als Ollama down is of het model ontbreekt."""
        url = f"{self.host.rstrip('/')}/api/tags"
        try:
            with httpx.Client(timeout=self._preflight_timeout) as client:
                resp = client.get(url)
            resp.raise_for_status()
            names = {item.get("name", "") for item in resp.json().get("models", [])}
        except Exception as exc:
            raise Layer3BackendError(
                f"Ollama niet bereikbaar op {self.host}: {type(exc).__name__}: {exc}\n"
                f"{self.unavailable_hint()}"
            ) from exc

        if not _model_present(self.model, names):
            raise Layer3BackendError(
                f"Ollama-model {self.model!r} niet aanwezig.\n{self.unavailable_hint()}"
            )

    def complete(self, system: str, user: str) -> str:
        """Qwen3.5-MLX: denk-modus uit + JSON normaliseren (leeg `content` anders)."""
        import ollama  # noqa: PLC0415

        client = ollama.Client(host=self.host)
        response = client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"{user}\n\n/no_think"},
            ],
            format="json",
            think=False,
            options={"temperature": 0.0},
        )
        msg = response["message"]
        raw = (msg.get("content") or "").strip()
        if not raw:
            # Zonder think=False komt Qwen3.5-output in `thinking`, niet in `content`.
            raw = (msg.get("thinking") or "").strip()
        extracted = _extract_json(raw)
        try:
            json.loads(extracted)
        except json.JSONDecodeError as exc:
            raise Layer3BackendError(
                f"Ollama MLX ({self.model}) gaf geen geldige JSON: {exc}. "
                "Zorg dat Ollama met MLX draait (OLLAMA_MLX=1 ollama serve) en up-to-date is."
            ) from exc
        return extracted


def _model_present(model: str, names: set[str]) -> bool:
    """Match `qwen3.5:2b-nvfp4` tegen tag-namen uit `/api/tags`."""
    if model in names:
        return True
    base, _, tag = model.partition(":")
    for name in names:
        if name == model or name.startswith(f"{model}:"):
            return True
        if tag and name in {f"{base}:{tag}", f"{base}:{tag}:latest"}:
            return True
    return False
