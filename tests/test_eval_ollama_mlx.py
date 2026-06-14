"""Tests voor Ollama-MLX eval-backend (TESTPLAN §8)."""

from __future__ import annotations

import json

import pytest

from eval.cli import _build_runner
from eval.runners.ollama_mlx_backend import (
    OllamaMlxEvalBackend,
    _model_present,
    ollama_mlx_start_hint,
)
from proxy.detection import Layer3BackendError


def test_model_present_matches_exact_and_prefixed_tags() -> None:
    names = {"qwen3.5:2b-nvfp4", "qwen3:1.7b"}
    assert _model_present("qwen3.5:2b-nvfp4", names)
    assert not _model_present("qwen3.5:4b-nvfp4", names)


def test_ollama_mlx_start_hint_mentions_env_and_pull() -> None:
    hint = ollama_mlx_start_hint(host="http://localhost:11434", model="qwen3.5:2b-nvfp4")
    assert "OLLAMA_MLX=1" in hint
    assert "qwen3.5:2b-nvfp4" in hint


def test_ollama_mlx_backend_fail_hard_without_server() -> None:
    backend = OllamaMlxEvalBackend(host="http://127.0.0.1:1", model="qwen3.5:2b-nvfp4")
    with pytest.raises(Layer3BackendError, match="Ollama niet bereikbaar"):
        backend.ensure_available()


def test_build_runner_ollama_mlx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OllamaMlxEvalBackend,
        "ensure_available",
        lambda self: None,
    )
    runner = _build_runner("pylades_md_ollama_mlx")
    assert runner.name == "pylades_md_ollama_mlx"
    assert runner.use_llm is True
    assert runner.llm_model == "qwen3.5:2b-nvfp4"


def test_ollama_mlx_complete_disables_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _FakeClient:
        def __init__(self, host: str) -> None:
            captured["host"] = host

        def chat(self, **kwargs: object) -> dict:
            captured.update(kwargs)
            return {"message": {"content": '{"entities": []}'}}

    monkeypatch.setattr("ollama.Client", _FakeClient)

    backend = OllamaMlxEvalBackend(host="http://localhost:11434", model="qwen3.5:2b-nvfp4")
    out = backend.complete("system", "Metformine 500mg")

    assert out == '{"entities": []}'
    assert captured["think"] is False
    assert captured["format"] == "json"
    user_msg = captured["messages"][1]["content"]
    assert user_msg.startswith("Metformine 500mg")
    assert user_msg.endswith("/no_think")


def test_ollama_mlx_complete_falls_back_to_thinking_field(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self, host: str) -> None:
            pass

        def chat(self, **kwargs: object) -> dict:
            return {
                "message": {
                    "content": "",
                    "thinking": (
                        '{"entities": [{"text": "X", "type": "product", "confidence": 0.9}]}'
                    ),
                }
            }

    monkeypatch.setattr("ollama.Client", _FakeClient)

    backend = OllamaMlxEvalBackend(model="qwen3.5:2b-nvfp4")
    out = backend.complete("system", "tekst")
    assert json.loads(out)["entities"][0]["text"] == "X"


def test_ollama_mlx_complete_raises_on_empty_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeClient:
        def __init__(self, host: str) -> None:
            pass

        def chat(self, **kwargs: object) -> dict:
            return {"message": {"content": "", "thinking": ""}}

    monkeypatch.setattr("ollama.Client", _FakeClient)

    backend = OllamaMlxEvalBackend(model="qwen3.5:2b-nvfp4")
    with pytest.raises(Layer3BackendError, match="geen geldige JSON"):
        backend.complete("system", "tekst")
