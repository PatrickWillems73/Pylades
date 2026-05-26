"""Tests voor `proxy/audit.py` (BR-G01)."""

from __future__ import annotations

from pathlib import Path

import pytest

import proxy.audit as audit_mod
from proxy.audit import (
    get_log_by_id,
    get_logs_by_session,
    get_recent_logs,
    log_request,
)
from shared.config import settings
from shared.db import init_databases


@pytest.fixture
def audit_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def test_log_request_stores_all_fields(audit_env: None) -> None:
    new_id = log_request(
        session_id="sess-1",
        original_prompt="Mevrouw Pietersen, BSN 123456782",
        pseudonymized_prompt="Mevrouw [PER-abcdef], BSN [BSN-aaaaaa]",
        template_id=None,
        response_pseudonymized="Antwoord met [PER-abcdef]",
        response_depseudonymized="Antwoord met Pietersen",
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-5",
        avg_confidence=0.95,
        review_required=False,
        error=None,
    )
    assert new_id > 0

    entry = get_log_by_id(new_id)
    assert entry is not None
    assert entry.id == new_id
    assert entry.session_id == "sess-1"
    assert entry.original_prompt.endswith("BSN 123456782")
    assert entry.pseudonymized_prompt.endswith("[BSN-aaaaaa]")
    assert entry.response_pseudonymized == "Antwoord met [PER-abcdef]"
    assert entry.response_depseudonymized == "Antwoord met Pietersen"
    assert entry.llm_provider == "anthropic"
    assert entry.llm_model == "claude-sonnet-4-5"
    assert entry.avg_confidence == pytest.approx(0.95)
    assert entry.review_required is False
    assert entry.error is None
    assert entry.created_at is not None


def test_log_request_creates_session_via_fk(audit_env: None) -> None:
    # Het feit dat dit slaagt bewijst dat _ensure_session de FK heeft
    # bevredigd; geen IntegrityError op een onbekende session_id.
    new_id = log_request(
        session_id="brand-new-session",
        original_prompt="x",
        pseudonymized_prompt="y",
    )
    assert new_id > 0


def test_review_required_roundtrips_as_bool(audit_env: None) -> None:
    new_id = log_request(
        session_id="sess-2",
        original_prompt="x",
        pseudonymized_prompt="y",
        review_required=True,
    )
    entry = get_log_by_id(new_id)
    assert entry is not None
    assert entry.review_required is True


def test_error_field_persisted(audit_env: None) -> None:
    new_id = log_request(
        session_id="sess-err",
        original_prompt="x",
        pseudonymized_prompt="y",
        error="anthropic 429 rate-limited",
    )
    entry = get_log_by_id(new_id)
    assert entry is not None
    assert entry.error == "anthropic 429 rate-limited"
    assert entry.response_pseudonymized is None
    assert entry.response_depseudonymized is None


def test_get_recent_logs_newest_first_and_limit(audit_env: None) -> None:
    ids = [
        log_request(
            session_id="sess-recent",
            original_prompt=f"prompt {i}",
            pseudonymized_prompt=f"pseudo {i}",
        )
        for i in range(5)
    ]
    recent = get_recent_logs(limit=3)
    assert [e.id for e in recent] == list(reversed(ids))[:3]


def test_get_recent_logs_clamps_limit(audit_env: None) -> None:
    log_request(session_id="s", original_prompt="x", pseudonymized_prompt="y")
    assert len(get_recent_logs(limit=0)) == 1  # geclampt naar 1
    assert len(get_recent_logs(limit=10_000)) == 1  # geclampt naar 1000, max 1 rij


def test_get_log_by_id_returns_none_for_missing(audit_env: None) -> None:
    assert get_log_by_id(424_242) is None


def test_get_logs_by_session_chronological(audit_env: None) -> None:
    s = "sess-chrono"
    ids = [
        log_request(
            session_id=s,
            original_prompt=f"p{i}",
            pseudonymized_prompt=f"q{i}",
        )
        for i in range(3)
    ]
    log_request(session_id="other", original_prompt="x", pseudonymized_prompt="y")
    fetched = get_logs_by_session(s)
    assert [e.id for e in fetched] == ids


def test_audit_module_does_not_import_vault_helper() -> None:
    """Spiegel-check op de AST-test in test_db_separation.

    Tweede signaal voor het geval iemand de AST-check wegcommenteert; deze
    inspecteert het audit-module *runtime* en valideert dat er geen
    vault-symbool in z'n namespace zit.
    """
    assert not hasattr(audit_mod, "get_vault_connection")
