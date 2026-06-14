"""Tests voor proxy/detection.py.

Drie groepen:
1. Per-regex-patroon: positief én negatief (validators rejecten echt).
2. Cross-cutting: overlap-resolutie, context-date-overrides, threshold-routing.
3. End-to-end op de 8 NL-fixtures.

spaCy- en Ollama-tests skippen wanneer hun model/server niet beschikbaar
is, zodat de suite in een minimal-CI-omgeving toch groen kan zijn.
"""

from __future__ import annotations

import pytest

from data.fixtures import (
    FIXTURES,
    INVALID_BSN,
    VALID_BSN,
    VALID_NL_IBAN,
    get_fixture,
)
from proxy.detection import (
    _LLM_TYPE_MAP,
    REGEX_PATTERNS,
    LayerStatus,
    LayerTiming,
    Thresholds,
    _get_spacy_nlp,
    _llm_entities_from_payload,
    _merge_cross_layer,
    detect_all,
    detect_all_timed,
    detect_llm,
    detect_regex,
    detect_spacy,
)
from shared.config import settings
from shared.models import DetectionLayer, Entity, EntityType


def _types_in(entities: list[Entity]) -> set[EntityType]:
    return {entity.entity_type for entity in entities}


def _spacy_available() -> bool:
    try:
        _get_spacy_nlp.cache_clear()
        _get_spacy_nlp()
        return True
    except OSError:
        return False
    except ImportError:
        return False


skip_no_spacy = pytest.mark.skipif(
    not _spacy_available(),
    reason=(
        f"spaCy-model {settings.spacy_model!r} niet geïnstalleerd — "
        "draai 'uv run python -m spacy download nl_core_news_md'"
    ),
)


# ---------------------------------------------------------------------------
# Per-laag timing (voortgangsindicator)
# ---------------------------------------------------------------------------


class TestDetectAllTimed:
    """`detect_all_timed` levert per-laag-timing + status, en draait callbacks."""

    def test_returns_timing_per_layer_and_marks_llm_disabled(self) -> None:
        result, timings = detect_all_timed(
            f"BSN {VALID_BSN}", use_llm=False, thresholds=Thresholds()
        )
        layers = [t.layer for t in timings]
        assert layers == [
            DetectionLayer.REGEX,
            DetectionLayer.SPACY,
            DetectionLayer.LLM,
        ]
        regex_timing = timings[0]
        assert regex_timing.status is LayerStatus.OK
        assert regex_timing.duration_ms is not None
        assert regex_timing.duration_ms >= 0.0
        # Laag 3 staat uit zonder use_llm.
        assert timings[2].status is LayerStatus.DISABLED
        assert timings[2].duration_ms is None
        # Resultaat identiek aan detect_all.
        plain = detect_all(f"BSN {VALID_BSN}", thresholds=Thresholds())
        assert {e.entity_type for e in result.confident_entities} == {
            e.entity_type for e in plain.confident_entities
        }

    def test_on_layer_callback_emits_running_then_done(self) -> None:
        snapshots: list[list[LayerTiming]] = []
        detect_all_timed(
            f"BSN {VALID_BSN}",
            use_llm=False,
            thresholds=Thresholds(),
            on_layer=lambda timings: snapshots.append(list(timings)),
        )
        # Eerste callback markeert regex als RUNNING.
        assert snapshots[0][-1].layer is DetectionLayer.REGEX
        assert snapshots[0][-1].status is LayerStatus.RUNNING
        # Laatste callback bevat drie afgeronde lagen (regex, spacy, llm-disabled).
        final = snapshots[-1]
        assert [t.layer for t in final] == [
            DetectionLayer.REGEX,
            DetectionLayer.SPACY,
            DetectionLayer.LLM,
        ]
        assert all(t.status is not LayerStatus.RUNNING for t in final)


# ---------------------------------------------------------------------------
# Per-regex-patroon
# ---------------------------------------------------------------------------


class TestRegexValidators:
    """BSN-elfproef en IBAN-mod-97 schakelen ongeldige matches uit."""

    def test_valid_bsn_passes_elfproef_and_is_detected(self) -> None:
        result = detect_regex(f"Patiënt BSN {VALID_BSN}.")
        bsn_entities = [e for e in result if e.entity_type is EntityType.BSN]
        assert len(bsn_entities) == 1
        assert bsn_entities[0].original == VALID_BSN
        assert bsn_entities[0].confidence == 1.0
        assert bsn_entities[0].detection_layer is DetectionLayer.REGEX

    def test_invalid_bsn_fails_elfproef_and_is_skipped(self) -> None:
        result = detect_regex(f"Order {INVALID_BSN} verstuurd.")
        assert EntityType.BSN not in _types_in(result), (
            "regex match maar elfproef faalt -> mag GEEN BSN-entity opleveren"
        )

    def test_valid_iban_passes_mod97_and_is_detected(self) -> None:
        result = detect_regex(f"IBAN {VALID_NL_IBAN}")
        ibans = [e for e in result if e.entity_type is EntityType.IBAN]
        assert len(ibans) == 1
        assert ibans[0].original == VALID_NL_IBAN

    def test_invalid_iban_checksum_is_rejected(self) -> None:
        # Pas één cijfer aan -> mod-97 faalt.
        broken = VALID_NL_IBAN[:-1] + ("1" if VALID_NL_IBAN[-1] != "1" else "2")
        result = detect_regex(f"IBAN {broken}")
        assert EntityType.IBAN not in _types_in(result)


class TestRegexBasicPatterns:
    """Eén positief geval per simpel patroon (volledige coverage volgt via fixtures)."""

    def test_email(self) -> None:
        result = detect_regex("Stuur naar test.user+spam@voorbeeld.nl morgen.")
        emails = [e for e in result if e.entity_type is EntityType.EMAIL]
        assert len(emails) == 1
        assert emails[0].original == "test.user+spam@voorbeeld.nl"

    def test_postcode_pc6_with_optional_space(self) -> None:
        result = detect_regex("Bezorgadres 1234 AB en daarna 5678CD")
        pc6s = sorted(
            (e for e in result if e.entity_type is EntityType.POSTCODE_PC6),
            key=lambda e: e.start,
        )
        assert [e.original for e in pc6s] == ["1234 AB", "5678CD"]

    def test_dutch_street_address(self) -> None:
        result = detect_regex("Bezoek op Doctor Kopstraat 1, 9697CF Boxmeer.")
        addresses = [e for e in result if e.entity_type is EntityType.ADDRESS]
        assert len(addresses) == 1
        assert addresses[0].original == "Doctor Kopstraat 1"
        assert addresses[0].detection_layer is DetectionLayer.REGEX

    def test_phone_with_dash_separator(self) -> None:
        result = detect_regex("Bel 06-12345678 voor info.")
        phones = [e for e in result if e.entity_type is EntityType.PHONE]
        assert len(phones) == 1
        assert phones[0].original == "06-12345678"

    def test_kenteken(self) -> None:
        result = detect_regex("Kenteken 12-AB-345 is gespot.")
        plates = [e for e in result if e.entity_type is EntityType.KENTEKEN]
        assert len(plates) == 1
        assert plates[0].original == "12-AB-345"

    def test_mrn_compact_and_with_colon(self) -> None:
        result = detect_regex("MRN1234567 en MRN: 89012345 vergeleken.")
        mrns = sorted(
            (e for e in result if e.entity_type is EntityType.MRN),
            key=lambda e: e.start,
        )
        assert len(mrns) == 2
        assert mrns[0].original == "MRN1234567"

    def test_epd_id(self) -> None:
        result = detect_regex("Zie dossier EPD-789012 in het systeem.")
        epds = [e for e in result if e.entity_type is EntityType.EPD_ID]
        assert len(epds) == 1
        assert epds[0].original == "EPD-789012"

    def test_icd10_with_subcode(self) -> None:
        result = detect_regex("Diagnose: J45.0 en E70.0 vastgesteld.")
        codes = sorted(
            (e for e in result if e.entity_type is EntityType.ICD10_CODE),
            key=lambda e: e.start,
        )
        assert [e.original for e in codes] == ["J45.0", "E70.0"]

    def test_age_with_jaar(self) -> None:
        result = detect_regex("Patient is 45 jaar oud, naast een 92-jarige.")
        ages = sorted(
            (e for e in result if e.entity_type is EntityType.AGE),
            key=lambda e: e.start,
        )
        assert [e.original for e in ages] == ["45 jaar", "92-jarige"]

    def test_age_digit_without_jaar_is_not_matched(self) -> None:
        result = detect_regex("Het lot was 89 of 95, niet te zeggen.")
        assert EntityType.AGE not in _types_in(result)

    def test_birthdate_without_admission_context(self) -> None:
        result = detect_regex("Geboren op 03-04-1972 in Utrecht.")
        dates = [e for e in result if e.entity_type is EntityType.BIRTHDATE]
        assert len(dates) == 1
        assert dates[0].original == "03-04-1972"


# ---------------------------------------------------------------------------
# Context-dates (BR-B04)
# ---------------------------------------------------------------------------


class TestContextDates:
    def test_admission_context_promotes_birthdate_to_admission(self) -> None:
        result = detect_regex("Patient opgenomen op 15-03-2024 in OLVG.")
        admissions = [e for e in result if e.entity_type is EntityType.ADMISSION_DATE]
        birthdates = [e for e in result if e.entity_type is EntityType.BIRTHDATE]
        assert len(admissions) == 1
        assert admissions[0].original == "15-03-2024"
        assert not birthdates, "BIRTHDATE mag niet meer pakken wat ADMISSION_DATE claimde"

    def test_discharge_context(self) -> None:
        result = detect_regex("Ontslag op 20-04-2024 met afspraak.")
        discharges = [e for e in result if e.entity_type is EntityType.DISCHARGE_DATE]
        assert len(discharges) == 1
        assert discharges[0].original == "20-04-2024"

    def test_exam_context(self) -> None:
        result = detect_regex("MRI scan op 10-05-2024 gepland.")
        exams = [e for e in result if e.entity_type is EntityType.EXAM_DATE]
        assert len(exams) == 1
        assert exams[0].original == "10-05-2024"

    def test_two_dates_one_admission_one_birthdate(self) -> None:
        text = "Geboren op 03-04-1972, opgenomen op 15-03-2024."
        result = detect_regex(text)
        types = _types_in(result)
        assert EntityType.ADMISSION_DATE in types
        assert EntityType.BIRTHDATE in types
        admissions = [e for e in result if e.entity_type is EntityType.ADMISSION_DATE]
        birthdates = [e for e in result if e.entity_type is EntityType.BIRTHDATE]
        assert admissions[0].original == "15-03-2024"
        assert birthdates[0].original == "03-04-1972"


# ---------------------------------------------------------------------------
# Overlap-resolutie + thresholds
# ---------------------------------------------------------------------------


class TestOverlapResolution:
    def test_epd_wins_over_project_for_overlap(self) -> None:
        # "EPD-789012" zou ook door PROJECT [A-Z]{2,5}-\d{2,6} matchen.
        # EPD_ID staat eerder in REGEX_PATTERNS en moet de span claimen.
        result = detect_regex("Zie EPD-789012 voor details.")
        types = _types_in(result)
        assert EntityType.EPD_ID in types
        assert EntityType.PROJECT not in types

    def test_cross_layer_merge_drops_overlapping_later_layer(self) -> None:
        # Synthese: regex BSN op span [0,9); een nepperige spaCy-entity op
        # span [3,7) wordt gedropt door _merge_cross_layer.
        regex_ent = Entity(
            original=VALID_BSN,
            entity_type=EntityType.BSN,
            confidence=1.0,
            detection_layer=DetectionLayer.REGEX,
            start=0,
            end=9,
        )
        spacy_ent = Entity(
            original="456782",
            entity_type=EntityType.NAME,
            confidence=0.9,
            detection_layer=DetectionLayer.SPACY,
            start=3,
            end=9,
        )
        merged = _merge_cross_layer([[regex_ent], [spacy_ent]])
        assert merged == [regex_ent]


class TestRoleContextNames:
    def test_detect_all_includes_role_context_name(self) -> None:
        text = "Verpleegkundig specialist: Okonkwo\nAnamnese:"
        result = detect_all(text, thresholds=Thresholds())
        names = [
            e.original
            for e in result.confident_entities
            if e.entity_type is EntityType.NAME
        ]
        assert "Okonkwo" in names


class TestThresholdRouting:
    def test_default_thresholds_send_regex_to_confident(self) -> None:
        result = detect_all(
            f"BSN {VALID_BSN}",
            thresholds=Thresholds(),
        )
        assert len(result.confident_entities) >= 1
        assert all(e.confidence >= 1.0 for e in result.confident_entities)
        assert not result.pending_review

    def test_raised_regex_threshold_forces_review(self) -> None:
        # 1.5 > 1.0 (regex confidence): elke regex-detectie naar review.
        result = detect_all(
            f"BSN {VALID_BSN} en EPD-789012",
            thresholds=Thresholds(regex=1.5),
        )
        assert not result.confident_entities
        assert len(result.pending_review) >= 2

    def test_threshold_lookup_per_layer(self) -> None:
        t = Thresholds(regex=0.5, spacy_person=0.95, spacy_org=0.6, llm=0.4)
        regex_ent = Entity(
            original="x",
            entity_type=EntityType.BSN,
            confidence=0.8,
            detection_layer=DetectionLayer.REGEX,
            start=0,
            end=1,
        )
        spacy_per = Entity(
            original="x",
            entity_type=EntityType.NAME,
            confidence=0.9,
            detection_layer=DetectionLayer.SPACY,
            start=0,
            end=1,
        )
        spacy_org = Entity(
            original="x",
            entity_type=EntityType.ORG,
            confidence=0.7,
            detection_layer=DetectionLayer.SPACY,
            start=0,
            end=1,
        )
        llm_ent = Entity(
            original="x",
            entity_type=EntityType.PRODUCT,
            confidence=0.5,
            detection_layer=DetectionLayer.LLM,
            start=0,
            end=1,
        )
        assert t.for_entity(regex_ent) == 0.5
        assert t.for_entity(spacy_per) == 0.95
        assert t.for_entity(spacy_org) == 0.6
        assert t.for_entity(llm_ent) == 0.4


# ---------------------------------------------------------------------------
# spaCy-laag
# ---------------------------------------------------------------------------


@skip_no_spacy
class TestSpaCyLayer:
    def test_finds_dutch_person_name(self) -> None:
        entities = detect_spacy("Patiënt heet Jan Pietersen en woont in Deventer.")
        names = [e for e in entities if e.entity_type is EntityType.NAME]
        assert names, "spaCy moet 'Jan Pietersen' als PER detecteren"
        assert any("Pietersen" in e.original for e in names)

    def test_finds_organization(self) -> None:
        entities = detect_spacy("OLVG en Catharina Ziekenhuis werken samen.")
        orgs = [e for e in entities if e.entity_type is EntityType.ORG]
        assert orgs, "spaCy moet OLVG of Catharina als ORG detecteren"


def test_detect_spacy_without_model_is_soft_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Forceer een OSError uit spacy.load om de soft-fail-tak te raken.
    monkeypatch.setattr(settings, "spacy_model", "nonexistent_model_zzz")
    _get_spacy_nlp.cache_clear()
    result = detect_spacy("Wat dan ook.")
    assert result == []
    _get_spacy_nlp.cache_clear()


# ---------------------------------------------------------------------------
# LLM-laag
# ---------------------------------------------------------------------------


class TestLLMLayer:
    def test_soft_fail_on_unreachable_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Poort 1 is gereserveerd; geen Ollama luistert daar. detect_llm
        # moet binnen redelijke tijd een lege lijst teruggeven, niet hangen.
        monkeypatch.setattr(settings, "ollama_host", "http://127.0.0.1:1")
        result = detect_llm("Test prompt met BSN 123456782 en projectcode PROJ-123.")
        assert result == []

    def test_payload_parser_accepts_valid_json(self) -> None:
        text = "We onderzoeken het medicijn AspirinePlus binnen PROJ-42."
        payload = {
            "entities": [
                {"text": "AspirinePlus", "type": "product", "confidence": 0.9},
                {"text": "PROJ-42", "type": "project", "confidence": 0.85},
            ]
        }
        entities = _llm_entities_from_payload(text, payload)
        types = _types_in(entities)
        assert EntityType.PRODUCT in types
        assert EntityType.PROJECT in types

    def test_payload_parser_rejects_unknown_type(self) -> None:
        entities = _llm_entities_from_payload(
            "Iets",
            {"entities": [{"text": "Iets", "type": "supernova", "confidence": 0.9}]},
        )
        assert entities == []

    def test_payload_parser_rejects_missing_span(self) -> None:
        # 'NotInText' staat niet in de tekst -> wordt geskipt.
        entities = _llm_entities_from_payload(
            "Korte tekst zonder match.",
            {"entities": [{"text": "NotInText", "type": "product", "confidence": 0.9}]},
        )
        assert entities == []

    def test_payload_parser_rejects_non_dict(self) -> None:
        assert _llm_entities_from_payload("x", []) == []
        assert _llm_entities_from_payload("x", "nope") == []


# ---------------------------------------------------------------------------
# End-to-end: 8 fixtures
# ---------------------------------------------------------------------------


_FIXTURE_EXPECTED_TYPES: dict[str, set[EntityType]] = {
    "basic_patient_snippet": {
        EntityType.BSN,
        EntityType.POSTCODE_PC6,
        EntityType.BIRTHDATE,
    },
    "clinical_note_mrn_epd": {
        EntityType.MRN,
        EntityType.EPD_ID,
        EntityType.ICD10_CODE,
        EntityType.ADMISSION_DATE,
    },
    "rare_icd_to_review": {EntityType.ICD10_CODE},
    "invalid_bsn_negative": set(),  # alleen NEGATIVE: niets aan regex mag matchen
    "age_boundary_above_and_below": {EntityType.AGE},
    "mixed_contact_details": {
        EntityType.EMAIL,
        EntityType.IBAN,
        EntityType.PHONE,
        EntityType.KENTEKEN,
    },
    "org_location_pc6_pair": {EntityType.POSTCODE_PC6},
    "rare_icd_with_quasi_identifier_stack": {EntityType.ICD10_CODE},
}


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda fx: fx.id)
def test_fixture_yields_expected_regex_types(fixture: object) -> None:
    fx = fixture  # type: ignore[assignment]
    expected = _FIXTURE_EXPECTED_TYPES[fx.id]  # type: ignore[attr-defined]
    # Hoge regex-threshold zou niets veranderen aan welke types verschijnen;
    # we testen alléén regex-laag-types via detect_regex (deterministisch,
    # geen spaCy-afhankelijkheid).
    result = detect_regex(fx.prompt)  # type: ignore[attr-defined]
    detected = _types_in(result)
    missing = expected - detected
    assert not missing, (
        f"{fx.id}: ontbrekende regex-types {missing}; gedetecteerd: {sorted(detected)}"
    )


def test_invalid_bsn_fixture_does_not_produce_bsn_entity() -> None:
    fx = get_fixture("invalid_bsn_negative")
    result = detect_regex(fx.prompt)
    types = _types_in(result)
    assert EntityType.BSN not in types, (
        "fixture invalid_bsn_negative bevat een 9-cijferig getal dat door "
        "elfproef afgewezen wordt; het mag niet als BSN-entity verschijnen"
    )


def test_clinical_note_fixture_distinguishes_admission_from_birthdate() -> None:
    fx = get_fixture("clinical_note_mrn_epd")
    result = detect_regex(fx.prompt)
    types = _types_in(result)
    assert EntityType.ADMISSION_DATE in types
    assert EntityType.BIRTHDATE not in types, (
        "datum hoort als ADMISSION_DATE geclaimd; BIRTHDATE mag er niet ook bij"
    )


# ---------------------------------------------------------------------------
# Module-internals sanity
# ---------------------------------------------------------------------------


def test_regex_patterns_cover_all_typecodes_we_claim_to_detect() -> None:
    # Sanity: voorkom dat iemand een patroon verwijdert zonder
    # bijbehorende test te updaten. Niet alle EntityTypes komen uit regex
    # (sommige uit spaCy/LLM/generalisering), maar de regex-laag claimt
    # tenminste deze set.
    covered = {entity_type for entity_type, _, _ in REGEX_PATTERNS}
    expected_regex_types = {
        EntityType.EMAIL,
        EntityType.IBAN,
        EntityType.BSN,
        EntityType.POSTCODE_PC6,
        EntityType.PHONE,
        EntityType.KENTEKEN,
        EntityType.MRN,
        EntityType.EPD_ID,
        EntityType.ICD10_CODE,
        EntityType.PROJECT,
        EntityType.AGE,
        EntityType.BIRTHDATE,
    }
    assert expected_regex_types <= covered


def test_llm_type_map_only_targets_product_and_project() -> None:
    # Laag 3 mag bewust niet alle types claimen die regex of spaCy al doen.
    assert set(_LLM_TYPE_MAP.values()) == {EntityType.PRODUCT, EntityType.PROJECT}
