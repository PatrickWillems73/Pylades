"""Tests voor de pluggable laag-3-backend en de MLX-backend (TESTPLAN §8).

We testen de seam (injectie van een alternatieve backend in `detect_all`) en de
MLX-specifieke robuustheid (think-strip + JSON-extractie, request/response) met
een gestubde HTTP-laag — geen draaiende MLX-server nodig.
"""

from __future__ import annotations

import json

import pytest

from eval.runners.mlx_backend import (
    MLXLayer3Backend,
    _extract_json,
    _normalize_json_response,
    _salvage_truncated_json,
)
from eval.runners.pylades_pipeline import PyladesPipelineRunner
from proxy.detection import Layer3BackendError, Thresholds, detect_all
from shared.models import EntityType


class _FakeBackend:
    """Laag-3-backend die een vaste JSON-payload teruggeeft (geen netwerk)."""

    name = "fake"
    model = "fake-model"

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return json.dumps(self._payload)


def test_injected_backend_feeds_layer3() -> None:
    text = "Patiënt gebruikt Wondermine 500mg dagelijks."
    backend = _FakeBackend({"entities": [{"text": "Wondermine", "type": "product"}]})

    result = detect_all(text, use_llm=True, thresholds=Thresholds(), llm_backend=backend)

    assert backend.calls, "backend.complete is niet aangeroepen"
    products = [
        e
        for e in (*result.confident_entities, *result.pending_review)
        if e.entity_type is EntityType.PRODUCT
    ]
    assert [e.original for e in products] == ["Wondermine"]


def test_layer3_disabled_skips_backend() -> None:
    backend = _FakeBackend({"entities": [{"text": "x", "type": "product"}]})
    detect_all("geen laag 3", use_llm=False, thresholds=Thresholds(), llm_backend=backend)
    assert backend.calls == []


def test_extract_json_strips_think_and_noise() -> None:
    raw = '<think>even nadenken…</think>\nHier: {"entities": []} klaar.'
    assert _extract_json(raw) == '{"entities": []}'


def test_extract_json_passthrough_plain_object() -> None:
    assert _extract_json('{"entities": [{"text": "A"}]}') == '{"entities": [{"text": "A"}]}'


def test_salvage_truncated_entities_array() -> None:
    truncated = (
        '{"entities": ['
        '{"text": "Iomeron 350", "type": "product", "confidence": 0.9}, '
        '{"text": "EPD-318609", "type": "product", "confidence": 0.8'
    )
    salvaged = _salvage_truncated_json(truncated)
    assert salvaged is not None
    payload = json.loads(salvaged)
    assert payload["entities"] == [
        {"text": "Iomeron 350", "type": "product", "confidence": 0.9}
    ]


def test_normalize_json_raises_on_truncation_without_salvage() -> None:
    truncated = '{"entities": [{"text": "open", "type": "product"'
    with pytest.raises(Layer3BackendError, match="afgekapt"):
        _normalize_json_response(truncated, finish_reason="length", max_tokens=1024)


def test_mlx_backend_builds_request_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {"content": '<think>x</think>{"entities": []}'},
                        "finish_reason": "stop",
                    }
                ]
            }

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def post(self, url: str, json: dict) -> _FakeResponse:  # noqa: A002
            captured["url"] = url
            captured["body"] = json
            return _FakeResponse()

    monkeypatch.setattr("eval.runners.mlx_backend.httpx.Client", _FakeClient)

    backend = MLXLayer3Backend(host="http://localhost:9999", model="test-mlx")
    out = backend.complete("system-prompt", "gebruikerstekst")

    assert out == '{"entities": []}'
    assert captured["url"] == "http://localhost:9999/v1/chat/completions"
    assert captured["body"]["model"] == "test-mlx"
    assert captured["body"]["messages"][0]["content"] == "system-prompt"
    user_msg = captured["body"]["messages"][1]["content"]
    assert user_msg.startswith("gebruikerstekst")
    assert user_msg.endswith("/no_think")
    assert "geldig JSON-object" in user_msg


def test_mlx_backend_fail_hard_without_server() -> None:
    backend = MLXLayer3Backend(host="http://127.0.0.1:1", model="test-mlx")
    with pytest.raises(Layer3BackendError, match="MLX-server niet bereikbaar"):
        backend.ensure_available()
    hint = backend.unavailable_hint()
    assert "mlx_lm.server" in hint
    assert "test-mlx" in hint
    assert "curl -s" in hint


def test_pylades_mlx_runner_fails_without_server() -> None:
    with pytest.raises(Layer3BackendError, match="MLX-server niet bereikbaar"):
        PyladesPipelineRunner(
            name="pylades_md_mlx",
            use_llm=True,
            llm_backend=MLXLayer3Backend(host="http://127.0.0.1:1", model="test-mlx"),
        )
