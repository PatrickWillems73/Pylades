"""Tests voor `proxy/mapping.PseudonymManager` (vault, BR-G02)."""

from __future__ import annotations

import pytest

from proxy.mapping import (
    PseudonymManager,
    export_mappings_csv,
    replace_two_way_pseudonyms,
)
from shared.config import settings
from shared.db import get_vault_connection, init_databases
from shared.models import DetectionLayer, Entity, EntityType, PseudonymizationMode


@pytest.fixture
def vault_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def _entity_bsn() -> Entity:
    return Entity(
        original="123456782",
        entity_type=EntityType.BSN,
        confidence=1.0,
        detection_layer=DetectionLayer.REGEX,
        start=0,
        end=9,
    )


def test_same_session_same_pseudonym_for_identical_original(
    vault_env: None,
) -> None:
    mgr = PseudonymManager.from_session("sess-a")
    ent = _entity_bsn()
    p1 = mgr.add_entity(ent, PseudonymizationMode.ONE_WAY)
    mgr.persist()
    p2 = mgr.add_entity(ent, PseudonymizationMode.ONE_WAY)
    assert p1 == p2


def test_different_session_yields_different_pseudonym(
    vault_env: None,
) -> None:
    ent = _entity_bsn()
    m1 = PseudonymManager.from_session("sess-1")
    p1 = m1.add_entity(ent, PseudonymizationMode.ONE_WAY)
    m1.persist()
    m2 = PseudonymManager.from_session("sess-2")
    p2 = m2.add_entity(ent, PseudonymizationMode.ONE_WAY)
    m2.persist()
    assert p1 != p2


def test_same_original_different_entity_type_distinct_row(
    vault_env: None,
) -> None:
    """UNIQUE(session_id, original, entity_type) — zelfde string, ander type."""
    shared_text = "123456789"
    name = Entity(
        original=shared_text,
        entity_type=EntityType.NAME,
        confidence=0.9,
        detection_layer=DetectionLayer.SPACY,
        start=0,
        end=9,
    )
    org = Entity(
        original=shared_text,
        entity_type=EntityType.ORG,
        confidence=0.9,
        detection_layer=DetectionLayer.SPACY,
        start=10,
        end=19,
    )
    mgr = PseudonymManager.from_session("sess-mix")
    pn = mgr.add_entity(name, PseudonymizationMode.ONE_WAY)
    po = mgr.add_entity(org, PseudonymizationMode.ONE_WAY)
    mgr.persist()
    assert pn != po
    with get_vault_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM mappings WHERE session_id = ?",
            ("sess-mix",),
        ).fetchone()
    assert int(n["c"]) == 2


def test_persist_writes_all_modes(vault_env: None) -> None:
    mgr = PseudonymManager.from_session("sess-modes")
    e1 = _entity_bsn()
    e2 = Entity(
        original="Jan Jansen",
        entity_type=EntityType.NAME,
        confidence=0.9,
        detection_layer=DetectionLayer.SPACY,
        start=0,
        end=10,
    )
    mgr.add_entity(e1, PseudonymizationMode.ONE_WAY)
    mgr.add_entity(e2, PseudonymizationMode.TWO_WAY)
    mgr.persist()
    with get_vault_connection() as conn:
        rows = conn.execute(
            "SELECT pseudonymization_mode FROM mappings WHERE session_id = ? ORDER BY entity_type",
            ("sess-modes",),
        ).fetchall()
    modes = {str(r["pseudonymization_mode"]) for r in rows}
    assert modes == {"one_way", "two_way"}


def test_replace_two_way_prefers_longest_pseudonym_first() -> None:
    text = "prefix[ZZ-abcdef]-extra suffix"
    out = replace_two_way_pseudonyms(
        text,
        [
            ("[ZZ-abcdef]-extra", "FULL"),
            ("[ZZ-abcdef]", "BAD"),
        ],
    )
    assert "FULL" in out
    assert "BAD" not in out
    assert "[ZZ-abcdef]" not in out


def test_export_mappings_csv_header_and_rows(vault_env: None) -> None:
    """CSV bevat de zeven verwachte kolommen plus één rij per mapping."""
    mgr = PseudonymManager.from_session("sess-csv")
    bsn = _entity_bsn()
    mgr.add_entity(bsn, PseudonymizationMode.ONE_WAY)
    name = Entity(
        original="Pietersen",
        entity_type=EntityType.NAME,
        confidence=0.95,
        detection_layer=DetectionLayer.SPACY,
        start=0,
        end=9,
    )
    mgr.add_entity(name, PseudonymizationMode.TWO_WAY)
    mgr.persist()

    csv_text = export_mappings_csv()
    lines = csv_text.strip().splitlines()
    header = lines[0]
    for column in (
        "session_id",
        "pseudonym",
        "original",
        "entity_type",
        "entity_category",
        "pseudonymization_mode",
        "created_at",
    ):
        assert column in header
    assert len(lines) == 3  # header + 2 mappings
    assert "Pietersen" in csv_text
    assert "123456782" in csv_text
    assert "one_way" in csv_text
    assert "two_way" in csv_text


def test_deanonymize_on_manager(vault_env: None) -> None:
    mgr = PseudonymManager.from_session("sess-d")
    ent = Entity(
        original="geheim@voorbeeld.nl",
        entity_type=EntityType.EMAIL,
        confidence=1.0,
        detection_layer=DetectionLayer.REGEX,
        start=0,
        end=20,
    )
    pseudo = mgr.add_entity(ent, PseudonymizationMode.TWO_WAY)
    mgr.persist()
    restored = mgr.deanonymize(f"Hallo {pseudo} einde")
    assert "geheim@voorbeeld.nl" in restored
    assert pseudo not in restored
