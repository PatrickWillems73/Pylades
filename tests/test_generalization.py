"""Tests voor proxy/generalization.py (BR-B01..B05)."""

from __future__ import annotations

import pytest

from data.fixtures import FIXTURES, get_fixture
from proxy.detection import detect_regex
from proxy.generalization import (
    GeneralizationConfig,
    _map_position,
    _parse_date_triplet,
    flag_rare_diagnoses,
    generalize_age,
    generalize_all,
    generalize_birthdate,
    generalize_postcode,
    generalize_treatment_dates,
)
from shared.models import DetectionLayer, Entity, EntityType


def _make_ent(
    *,
    text: str,
    entity_type: EntityType,
    start: int,
    end: int | None = None,
) -> Entity:
    end = end if end is not None else start + len(text)
    return Entity(
        original=text,
        entity_type=entity_type,
        confidence=1.0,
        detection_layer=DetectionLayer.REGEX,
        start=start,
        end=end,
    )


def test_parse_date_accepts_slash_and_dash() -> None:
    assert _parse_date_triplet("15-03-2024") == (15, 3, 2024)
    assert _parse_date_triplet("03/04/1972") == (3, 4, 1972)
    assert _parse_date_triplet("no-date") is None


def test_generalize_birthdate_replaces_year_only() -> None:
    text = "Geboren op 03-04-1972 in Deventer."
    birth = _make_ent(
        text="03-04-1972",
        entity_type=EntityType.BIRTHDATE,
        start=text.index("03-04-1972"),
    )
    other = _make_ent(
        text="Deventer",
        entity_type=EntityType.LOCATION,
        start=text.index("Deventer"),
    )
    cfg = GeneralizationConfig()
    new_t, new_es = generalize_birthdate(text, [birth, other], cfg)
    assert "1972" in new_t
    assert "03-04-1972" not in new_t
    by_year = [e for e in new_es if e.entity_type is EntityType.BIRTH_YEAR]
    assert len(by_year) == 1
    assert by_year[0].original == "03-04-1972"
    assert by_year[0].generalized_to == "1972"
    loc = [e for e in new_es if e.entity_type is EntityType.LOCATION][0]
    assert new_t[loc.start : loc.end] == "Deventer"


def test_generalize_postcode_pc6_to_pc2() -> None:
    text = "Postcode 7411AB voor levering"
    pc = _make_ent(
        text="7411AB",
        entity_type=EntityType.POSTCODE_PC6,
        start=text.index("7411AB"),
    )
    new_t, new_es = generalize_postcode(text, [pc], GeneralizationConfig())
    assert "74 " in new_t or new_t.endswith("74 voor") or "74 voor" in new_t
    assert "7411AB" not in new_t
    assert new_es[0].entity_type is EntityType.POSTCODE_PC2
    assert new_es[0].generalized_to == "74"


def test_generalize_postcode_handles_spaced_pc6() -> None:
    text = "Ik woon op 1091 AC"
    # Spans also via detectiepatroon met spatie:
    pc = _make_ent(
        text="1091 AC",
        entity_type=EntityType.POSTCODE_PC6,
        start=text.index("1091 AC"),
    )
    new_t, new_es = generalize_postcode(text, [pc], GeneralizationConfig())
    assert "10" in new_t
    assert new_es[0].generalized_to == "10"


def test_generalize_age_boundary() -> None:
    text = "89 jaar en 95 jaar samen"
    e89 = _make_ent(
        text="89 jaar",
        entity_type=EntityType.AGE,
        start=text.index("89 jaar"),
    )
    e95 = _make_ent(
        text="95 jaar",
        entity_type=EntityType.AGE,
        start=text.index("95 jaar"),
    )
    new_t, new_es = generalize_age(text, [e89, e95], GeneralizationConfig())
    assert "89 jaar" in new_t
    assert "90+ jaar" in new_t
    assert "95 jaar" not in new_t
    assert all(e.entity_type is EntityType.AGE for e in new_es)


def test_generalize_age_jarige_suffix() -> None:
    text = "Een 92-jarige patiënt"
    ent = _make_ent(
        text="92-jarige",
        entity_type=EntityType.AGE,
        start=text.index("92-jarige"),
    )
    new_t, _ = generalize_age(text, [ent], GeneralizationConfig())
    assert "90+ jarige" in new_t


def test_generalize_treatment_date_op_to_in() -> None:
    text = "werd opgenomen op 15-03-2024 met astma"
    adm = _make_ent(
        text="15-03-2024",
        entity_type=EntityType.ADMISSION_DATE,
        start=text.index("15-03-2024"),
    )
    new_t, new_es = generalize_treatment_dates(text, [adm], GeneralizationConfig())
    assert "opgenomen in 2024-03" in new_t.replace("  ", " ")
    assert "15-03-2024" not in new_t
    assert new_es[0].generalized_to == " in 2024-03"
    assert new_es[0].entity_type is EntityType.ADMISSION_DATE


def test_flag_rare_icd_sets_review_bit() -> None:
    text = "Diagnose G71.0"
    icd = _make_ent(
        text="G71.0",
        entity_type=EntityType.ICD10_CODE,
        start=text.index("G71.0"),
    )
    _t, out = flag_rare_diagnoses(text, [icd], GeneralizationConfig())
    assert out[0].rare_icd_review is True


def test_flag_rare_icd_common_code_unset() -> None:
    text = "J45.0 astma"
    icd = _make_ent(
        text="J45.0",
        entity_type=EntityType.ICD10_CODE,
        start=text.index("J45.0"),
    )
    _t, out = flag_rare_diagnoses(text, [icd], GeneralizationConfig())
    assert out[0].rare_icd_review is False


def test_config_disables_birthdate() -> None:
    text = "Geboren op 03-04-1972"
    birth = _make_ent(
        text="03-04-1972",
        entity_type=EntityType.BIRTHDATE,
        start=text.index("03-04-1972"),
    )
    cfg = GeneralizationConfig(birthdate=False)
    new_t, new_es = generalize_birthdate(text, [birth], cfg)
    assert new_t == text
    assert new_es[0].entity_type is EntityType.BIRTHDATE


def test_map_position_stacking() -> None:
    edits = [(0, 3, "x"), (10, 12, "yy")]
    assert _map_position(5, edits) == 5 + (1 - 3)
    assert _map_position(15, edits) == 15 + (1 - 3) + (2 - 2)


def test_fixture_basic_patient_pipeline() -> None:
    fx = get_fixture("basic_patient_snippet")
    entities = detect_regex(fx.prompt)
    new_t, out = generalize_all(fx.prompt, entities, GeneralizationConfig())
    assert "1972" in new_t
    assert "03-04-1972" not in new_t
    assert "7411AB" not in new_t.upper()
    assert "74" in new_t
    assert any(e.entity_type is EntityType.BIRTH_YEAR for e in out)
    assert any(e.entity_type is EntityType.POSTCODE_PC2 for e in out)


def test_fixture_clinical_admission() -> None:
    fx = get_fixture("clinical_note_mrn_epd")
    entities = detect_regex(fx.prompt)
    new_t, _out = generalize_all(fx.prompt, entities, GeneralizationConfig())
    assert "2024-03" in new_t
    assert "15-03-2024" not in new_t
    assert "opgenomen in" in new_t


def test_fixture_age_boundary_pipeline() -> None:
    fx = get_fixture("age_boundary_above_and_below")
    entities = detect_regex(fx.prompt)
    new_t, _ = generalize_all(fx.prompt, entities, GeneralizationConfig())
    assert "89 jaar" in new_t
    assert "90+ jaar" in new_t
    assert "95 jaar" not in new_t


def test_fixture_org_two_postcodes() -> None:
    fx = get_fixture("org_location_pc6_pair")
    entities = detect_regex(fx.prompt)
    new_t, out = generalize_all(fx.prompt, entities, GeneralizationConfig())
    assert "1091AC" not in new_t.replace(" ", "")
    assert "5623EJ" not in new_t.replace(" ", "")
    pc2s = [e.generalized_to for e in out if e.entity_type is EntityType.POSTCODE_PC2]
    assert sorted(pc2s) == ["10", "56"]


def test_fixture_rare_icd_flags() -> None:
    fx = get_fixture("rare_icd_to_review")
    entities = detect_regex(fx.prompt)
    _t, out = generalize_all(fx.prompt, entities, GeneralizationConfig())
    g71 = next(e for e in out if e.entity_type is EntityType.ICD10_CODE and "G71" in e.original)
    assert g71.rare_icd_review is True


def test_fixture_clinical_note_common_icd_not_flagged() -> None:
    fx = get_fixture("clinical_note_mrn_epd")
    entities = detect_regex(fx.prompt)
    _t, out = generalize_all(fx.prompt, entities, GeneralizationConfig())
    j45 = next(e for e in out if e.original == "J45.0")
    assert j45.rare_icd_review is False


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fx: fx.id)
def test_generalize_all_runs_on_every_fixture(fixture: object) -> None:
    fx = fixture  # type: ignore[assignment]
    entities = detect_regex(fx.prompt)  # type: ignore[attr-defined]
    new_t, out = generalize_all(fx.prompt, entities, GeneralizationConfig())  # type: ignore[attr-defined]
    assert isinstance(new_t, str)
    assert len(out) == len(entities)
