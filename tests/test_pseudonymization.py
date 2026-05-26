"""Tests voor `proxy/pseudonymization.py` (BR-C01, BR-C06)."""

from __future__ import annotations

import pytest

from proxy.pseudonymization import (
    depseudonymize,
    get_super_default_pseudonymization_mode,
    pseudonymize,
    resolve_effective_mode,
    resolve_effective_mode_with_source,
)
from shared.config import settings
from shared.db import init_databases, set_config_value
from shared.models import DetectionLayer, Entity, EntityType, PseudonymizationMode, Template


@pytest.fixture
def pc_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "content.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "vault.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def _minimal_template(**kwargs: object) -> Template:
    base: dict[str, object] = {
        "groep": "g",
        "naam": "n",
        "beschrijving": "",
        "llm_provider": "anthropic",
        "llm_naam": "claude",
    }
    base.update(kwargs)
    return Template(**base)  # type: ignore[arg-type]


def test_resolve_effective_mode_layers() -> None:
    t = _minimal_template(
        default_mode=PseudonymizationMode.ONE_WAY,
        mode_overrides={EntityType.BSN: PseudonymizationMode.TWO_WAY},
        two_way_justification="need two way for BSN in test",
    )
    assert (
        resolve_effective_mode(t, EntityType.BSN, PseudonymizationMode.ONE_WAY)
        == PseudonymizationMode.TWO_WAY
    )
    assert (
        resolve_effective_mode(t, EntityType.NAME, PseudonymizationMode.ONE_WAY)
        == PseudonymizationMode.ONE_WAY
    )
    t2 = _minimal_template(default_mode=None, mode_overrides={})
    assert (
        resolve_effective_mode(t2, EntityType.IBAN, PseudonymizationMode.TWO_WAY)
        == PseudonymizationMode.TWO_WAY
    )


def test_resolve_effective_mode_with_source_labels() -> None:
    t = _minimal_template(
        default_mode=PseudonymizationMode.TWO_WAY,
        mode_overrides={EntityType.BSN: PseudonymizationMode.ONE_WAY},
        two_way_justification="needed elsewhere",
    )
    # override-pad
    mode, source = resolve_effective_mode_with_source(
        t, EntityType.BSN, PseudonymizationMode.ONE_WAY
    )
    assert (mode, source) == (PseudonymizationMode.ONE_WAY, "override")
    # template-default-pad
    mode, source = resolve_effective_mode_with_source(
        t, EntityType.NAME, PseudonymizationMode.ONE_WAY
    )
    assert (mode, source) == (PseudonymizationMode.TWO_WAY, "template-default")
    # super-default-pad
    t2 = _minimal_template()
    mode, source = resolve_effective_mode_with_source(
        t2, EntityType.IBAN, PseudonymizationMode.ONE_WAY
    )
    assert (mode, source) == (PseudonymizationMode.ONE_WAY, "super-default")


def test_super_default_from_config(pc_env: None) -> None:
    set_config_value("super_default_pseudonymization_mode", "two_way")
    assert get_super_default_pseudonymization_mode() == PseudonymizationMode.TWO_WAY


def test_pseudonymize_substitutes_text(pc_env: None) -> None:
    text = "BSN 123456782 klaar"
    ent = Entity(
        original="123456782",
        entity_type=EntityType.BSN,
        confidence=1.0,
        detection_layer=DetectionLayer.REGEX,
        start=text.index("123456782"),
        end=text.index("123456782") + len("123456782"),
    )
    tpl = _minimal_template()
    new_text, out = pseudonymize(text, [ent], "s1", tpl)
    assert "[BSN-" in new_text
    assert "123456782" not in new_text
    assert out[0].pseudonym is not None
    assert out[0].effective_mode == PseudonymizationMode.ONE_WAY


def test_depseudonymize_only_two_way(pc_env: None) -> None:
    text = "Een 123456782 en een naam"
    bsn = Entity(
        original="123456782",
        entity_type=EntityType.BSN,
        confidence=1.0,
        detection_layer=DetectionLayer.REGEX,
        start=text.index("123456782"),
        end=text.index("123456782") + len("123456782"),
    )
    name = Entity(
        original="naam",
        entity_type=EntityType.NAME,
        confidence=0.95,
        detection_layer=DetectionLayer.SPACY,
        start=text.index("naam"),
        end=text.index("naam") + len("naam"),
    )
    tpl = _minimal_template(
        mode_overrides={
            EntityType.BSN: PseudonymizationMode.ONE_WAY,
            EntityType.NAME: PseudonymizationMode.TWO_WAY,
        },
        two_way_justification="NAME is reversible for test",
    )
    new_text, updated = pseudonymize(text, [bsn, name], "sess-7", tpl)
    bsn_p = next(u.pseudonym for u in updated if u.entity_type == EntityType.BSN)
    name_p = next(u.pseudonym for u in updated if u.entity_type == EntityType.NAME)
    assert bsn_p in new_text and name_p in new_text
    back = depseudonymize(new_text, "sess-7")
    assert "123456782" not in back
    assert "naam" in back
    assert bsn_p in back
