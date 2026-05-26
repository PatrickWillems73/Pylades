"""Tests voor `ui/audit_format.py`."""

from __future__ import annotations

from shared.models import AuditEntry
from ui.audit_format import StatusBadge, pretty_json, status_badge


def _entry(**overrides: object) -> AuditEntry:
    base: dict[str, object] = {
        "session_id": "sess-x",
        "original_prompt": "prompt",
        "pseudonymized_prompt": "[PER-abcdef]",
    }
    base.update(overrides)
    return AuditEntry(**base)  # type: ignore[arg-type]


def test_status_badge_error_wins_over_review() -> None:
    entry = _entry(error="upstream HTTP 500", review_required=True)
    assert status_badge(entry) == StatusBadge(label="error", tone="error")


def test_status_badge_review_when_no_error() -> None:
    entry = _entry(review_required=True)
    assert status_badge(entry) == StatusBadge(label="review", tone="warning")


def test_status_badge_ok_default() -> None:
    entry = _entry()
    assert status_badge(entry) == StatusBadge(label="ok", tone="success")


def test_pretty_json_formats_valid_json() -> None:
    raw = '{"content":[{"text":"hi"}],"model":"claude-3-haiku"}'
    out = pretty_json(raw)
    assert "\n" in out
    assert '"model": "claude-3-haiku"' in out


def test_pretty_json_echoes_non_json_string() -> None:
    raw = "kaal text, geen JSON"
    assert pretty_json(raw) == raw


def test_pretty_json_handles_none_and_empty() -> None:
    assert pretty_json(None) == ""
    assert pretty_json("") == ""


def test_pretty_json_preserves_unicode() -> None:
    raw = '{"naam":"Pietersen","plaats":"Den Haag"}'
    out = pretty_json(raw)
    assert "Pietersen" in out
    assert "Den Haag" in out
