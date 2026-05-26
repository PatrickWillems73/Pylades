"""Tests voor `proxy/templates.py` CRUD-laag."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from proxy.audit import log_request
from proxy.templates import (
    delete_template,
    get_template,
    list_templates,
    move_template,
    upsert_template,
)
from shared.config import settings
from shared.db import init_databases
from shared.models import EntityType, PseudonymizationMode, Template


@pytest.fixture
def tpl_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def _new_template(**overrides: object) -> Template:
    base: dict[str, object] = {
        "groep": "klinisch",
        "naam": "Standaard ontslag-brief",
        "beschrijving": "Demo-template",
        "llm_provider": "anthropic",
        "llm_naam": "claude-sonnet-4-5",
        "prompt_tekst": "Vat dit dossier samen: {input}",
    }
    base.update(overrides)
    return Template(**base)  # type: ignore[arg-type]


def test_upsert_insert_then_list(tpl_env: None) -> None:
    new_id = upsert_template(_new_template())
    assert new_id > 0
    items = list_templates()
    assert len(items) == 1
    assert items[0].id == new_id
    assert items[0].naam == "Standaard ontslag-brief"


def test_upsert_update_preserves_id(tpl_env: None) -> None:
    new_id = upsert_template(_new_template())
    fetched = get_template(new_id)
    assert fetched is not None
    updated = fetched.model_copy(update={"beschrijving": "Aangepast"})
    same_id = upsert_template(updated)
    assert same_id == new_id
    refetched = get_template(new_id)
    assert refetched is not None
    assert refetched.beschrijving == "Aangepast"


def test_mode_overrides_roundtrip_through_json(tpl_env: None) -> None:
    new_id = upsert_template(
        _new_template(
            mode_overrides={
                EntityType.BSN: PseudonymizationMode.ONE_WAY,
                EntityType.NAME: PseudonymizationMode.TWO_WAY,
            },
            two_way_justification="case-study analysis",
        )
    )
    fetched = get_template(new_id)
    assert fetched is not None
    assert fetched.mode_overrides == {
        EntityType.BSN: PseudonymizationMode.ONE_WAY,
        EntityType.NAME: PseudonymizationMode.TWO_WAY,
    }
    assert fetched.two_way_justification == "case-study analysis"


def test_two_way_without_justification_fails_validation(tpl_env: None) -> None:
    with pytest.raises(ValueError):
        _new_template(
            mode_overrides={EntityType.NAME: PseudonymizationMode.TWO_WAY},
        )


def test_delete_template_removes_row(tpl_env: None) -> None:
    new_id = upsert_template(_new_template())
    assert delete_template(new_id) is True
    assert get_template(new_id) is None
    assert list_templates() == []


def test_delete_unknown_template_returns_false(tpl_env: None) -> None:
    assert delete_template(424_242) is False


def test_empty_prompt_tekst_is_allowed(tpl_env: None) -> None:
    """Lege prompt blijft toegestaan voor halfaffe templates en migratie-edges."""
    new_id = upsert_template(_new_template(prompt_tekst=""))
    fetched = get_template(new_id)
    assert fetched is not None
    assert fetched.prompt_tekst == ""


def test_prompt_tekst_must_contain_input_placeholder() -> None:
    with pytest.raises(ValidationError):
        _new_template(prompt_tekst="Vat samen zonder placeholder.")


def test_prompt_tekst_must_have_exactly_one_input_placeholder() -> None:
    with pytest.raises(ValidationError):
        _new_template(prompt_tekst="{input} en nogmaals {input}.")


def test_prompt_tekst_rejects_other_placeholders() -> None:
    with pytest.raises(ValidationError):
        _new_template(prompt_tekst="Beste {naam}, hier: {input}")


def test_max_tokens_roundtrips(tpl_env: None) -> None:
    new_id = upsert_template(_new_template(max_tokens=4096))
    fetched = get_template(new_id)
    assert fetched is not None
    assert fetched.max_tokens == 4096


def test_max_tokens_default_is_set_on_insert(tpl_env: None) -> None:
    new_id = upsert_template(_new_template())
    fetched = get_template(new_id)
    assert fetched is not None
    assert fetched.max_tokens == 16_000


def test_max_tokens_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _new_template(max_tokens=0)


def test_use_llm_defaults_to_false_and_roundtrips(tpl_env: None) -> None:
    new_id = upsert_template(_new_template())
    fetched = get_template(new_id)
    assert fetched is not None
    assert fetched.use_llm is False


def test_use_llm_can_be_enabled(tpl_env: None) -> None:
    new_id = upsert_template(_new_template(use_llm=True))
    fetched = get_template(new_id)
    assert fetched is not None
    assert fetched.use_llm is True


def test_use_llm_update_toggles_value(tpl_env: None) -> None:
    new_id = upsert_template(_new_template(use_llm=True))
    fetched = get_template(new_id)
    assert fetched is not None
    same_id = upsert_template(fetched.model_copy(update={"use_llm": False}))
    assert same_id == new_id
    refetched = get_template(new_id)
    assert refetched is not None
    assert refetched.use_llm is False


def test_delete_template_with_audit_rows_keeps_audit(tpl_env: None) -> None:
    """ON DELETE SET NULL: audit_log.template_id wordt NULL, rij blijft."""
    new_id = upsert_template(_new_template())
    audit_id = log_request(
        session_id="sess-del",
        original_prompt="foo",
        pseudonymized_prompt="bar",
        template_id=new_id,
    )
    assert delete_template(new_id) is True

    from proxy.audit import get_log_by_id  # noqa: PLC0415

    entry = get_log_by_id(audit_id)
    assert entry is not None
    assert entry.template_id is None


def test_new_template_gets_sort_order_at_end(tpl_env: None) -> None:
    first_id = upsert_template(_new_template(naam="Alpha"))
    second_id = upsert_template(_new_template(naam="Beta"))
    items = list_templates()
    assert [t.id for t in items] == [first_id, second_id]
    assert items[0].sort_order == 0
    assert items[1].sort_order == 1


def test_move_template_swaps_positions(tpl_env: None) -> None:
    first_id = upsert_template(_new_template(naam="Alpha"))
    second_id = upsert_template(_new_template(naam="Beta"))
    third_id = upsert_template(_new_template(naam="Gamma"))

    assert move_template(second_id, -1) is True
    assert [t.id for t in list_templates()] == [second_id, first_id, third_id]

    assert move_template(second_id, 1) is True
    assert [t.id for t in list_templates()] == [first_id, second_id, third_id]


def test_move_template_respects_bounds(tpl_env: None) -> None:
    new_id = upsert_template(_new_template())
    assert move_template(new_id, -1) is False
    assert move_template(new_id, 1) is False
    assert move_template(999_999, 1) is False


def test_sort_order_migration_backfills_alphabetical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bestaande DB zonder `sort_order` krijgt alfabetische volgorde bij migratie."""
    db_path = tmp_path / "legacy-content.db"
    monkeypatch.setattr(settings, "content_db_path", db_path)
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")

    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            groep TEXT NOT NULL,
            naam TEXT NOT NULL,
            beschrijving TEXT NOT NULL DEFAULT '',
            llm_provider TEXT NOT NULL,
            llm_naam TEXT NOT NULL,
            prompt_tekst TEXT NOT NULL DEFAULT '',
            max_tokens INTEGER NOT NULL DEFAULT 16000,
            use_llm INTEGER NOT NULL DEFAULT 0,
            default_mode TEXT,
            mode_overrides TEXT NOT NULL DEFAULT '{}',
            two_way_justification TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """INSERT INTO templates (
            groep, naam, beschrijving, llm_provider, llm_naam, prompt_tekst
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        ("z", "Zulu", "", "anthropic", "claude", "x: {input}"),
    )
    conn.execute(
        """INSERT INTO templates (
            groep, naam, beschrijving, llm_provider, llm_naam, prompt_tekst
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        ("a", "Alpha", "", "anthropic", "claude", "y: {input}"),
    )
    conn.commit()
    conn.close()

    init_databases()

    items = list_templates()
    assert [t.groep for t in items] == ["a", "z"]
    assert items[0].sort_order == 0
    assert items[1].sort_order == 1
