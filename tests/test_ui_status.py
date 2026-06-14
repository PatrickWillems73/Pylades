"""Tests voor `ui/status.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.config import settings
from shared.db import init_databases
from ui.status import (
    check_databases,
    check_deduce,
    check_ollama,
    check_proxy,
    run_all_checks,
)


def test_check_databases_missing_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "absent.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "ook-weg.db")
    result = check_databases()
    assert result.ok is False
    assert "absent.db" in result.message or "Ontbreekt" in result.message
    assert result.fix_command is not None


def test_check_databases_after_init_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()
    result = check_databases()
    assert result.ok is True
    assert result.fix_command is None


def test_check_proxy_returns_red_when_no_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Poort 1 is reserved; geen daemon luistert daar binnen 1s.
    monkeypatch.setattr(settings, "proxy_port", 1)
    result = check_proxy()
    assert result.ok is False
    assert "uvicorn" in (result.fix_command or "")


def test_check_ollama_returns_red_when_no_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ollama_host", "http://127.0.0.1:1")
    result = check_ollama()
    assert result.ok is False
    assert "ollama" in (result.fix_command or "").lower()


def test_check_deduce_red_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("proxy.deduce_layer.deduce_available", lambda: False)
    result = check_deduce()
    assert result.ok is False
    assert "uv sync" in (result.fix_command or "")


def test_run_all_checks_returns_four_cards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    monkeypatch.setattr(settings, "proxy_port", 1)
    monkeypatch.setattr(settings, "ollama_host", "http://127.0.0.1:1")
    monkeypatch.setattr("proxy.deduce_layer.deduce_available", lambda: False)
    init_databases()

    cards = run_all_checks()
    assert [c.name for c in cards] == ["Proxy", "Ollama", "DEDUCE", "Databases"]
    # Drie rood, één groen (databases).
    assert sum(1 for c in cards if c.ok) == 1
    assert any(c.name == "Databases" and c.ok for c in cards)
