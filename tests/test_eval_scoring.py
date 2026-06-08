"""Unit-tests voor de scoring-functies (geen spaCy/DB nodig)."""

from __future__ import annotations

from eval.metrics.scoring import (
    Counts,
    _coverage_fraction,
    confusion_pairs,
    counts_by_type,
    find_exposed,
    find_leaks,
    match_entities,
    over_redaction_count,
    precision_recall_f1,
)
from eval.runners.base import PredEntity
from eval.schema import EvalRecord, GoldEntity
from shared.models import EntityCategory, EntityType


def _gold(start: int, end: int, text: str, etype: EntityType) -> GoldEntity:
    return GoldEntity(start=start, end=end, text=text, type=etype)


def _pred(start: int, end: int, text: str, etype: EntityType) -> PredEntity:
    return PredEntity(
        start=start, end=end, text=text, type=etype, confidence=1.0, layer="regex"
    )


def test_exact_match_counts_tp() -> None:
    gold = [_gold(0, 9, "123456782", EntityType.BSN)]
    pred = [_pred(0, 9, "123456782", EntityType.BSN)]
    result = match_entities(gold, pred, "exact")
    counts = counts_by_type(gold, pred, result)
    assert counts[EntityType.BSN] == Counts(tp=1, fp=0, fn=0)


def test_exact_misses_boundary_but_overlap_matches() -> None:
    # Pred bevat de titel mee ("Dhr. Bakker") terwijl gold alleen "Bakker" is.
    gold = [_gold(5, 11, "Bakker", EntityType.NAME)]
    pred = [_pred(0, 11, "Dhr. Bakker", EntityType.NAME)]

    exact = counts_by_type(gold, pred, match_entities(gold, pred, "exact"))
    assert exact[EntityType.NAME] == Counts(tp=0, fp=1, fn=1)

    overlap = counts_by_type(gold, pred, match_entities(gold, pred, "overlap"))
    assert overlap[EntityType.NAME] == Counts(tp=1, fp=0, fn=0)


def test_confusion_pairs_capture_type_swap() -> None:
    gold = [_gold(0, 13, "Dorpsstraat 12", EntityType.ADDRESS)]
    pred = [_pred(0, 13, "Dorpsstraat 12", EntityType.LOCATION)]
    result = match_entities(gold, pred, "overlap")
    pairs = confusion_pairs(gold, pred, result)
    assert pairs == [(EntityType.ADDRESS, EntityType.LOCATION)]


def test_precision_recall_f1_zero_safe() -> None:
    assert precision_recall_f1(Counts(0, 0, 0)) == (0.0, 0.0, 0.0)
    p, r, f1 = precision_recall_f1(Counts(tp=1, fp=1, fn=0))
    assert p == 0.5 and r == 1.0 and round(f1, 4) == 0.6667


def test_coverage_fraction_partial_and_full() -> None:
    # Volledige dekking ook bij twee aangrenzende spans.
    adjacent = [_pred(0, 6, "x", EntityType.NAME), _pred(6, 10, "y", EntityType.NAME)]
    assert _coverage_fraction(0, 10, adjacent) == 1.0
    # Halve dekking.
    assert _coverage_fraction(0, 10, [_pred(0, 5, "x", EntityType.NAME)]) == 0.5
    # Geen dekking.
    assert _coverage_fraction(0, 10, []) == 0.0


def test_find_leaks_flags_uncovered_direct_identifier() -> None:
    record = EvalRecord(
        id="t1",
        prompt="BSN 123456782 hier.",
        entities=[_gold(4, 13, "123456782", EntityType.BSN)],
    )
    # Niets gedetecteerd -> volledige lek.
    leaks = find_leaks(record, [])
    assert len(leaks) == 1
    assert leaks[0].severity == "full"
    assert leaks[0].coverage == 0.0

    # Gedetecteerd -> geen lek.
    pred = [_pred(4, 13, "123456782", EntityType.BSN)]
    assert find_leaks(record, pred) == []


def test_find_exposed_clinical_category() -> None:
    record = EvalRecord(
        id="t2",
        prompt="Diagnose ALS hier.",
        entities=[_gold(9, 12, "ALS", EntityType.DIAGNOSIS)],
    )
    exposed = find_exposed(record, [], EntityCategory.CLINICAL_SENSITIVE)
    assert len(exposed) == 1
    # Geen direct-identifier -> telt niet als lek voor de gate.
    assert find_leaks(record, []) == []


def test_over_redaction_counts_spurious_prediction() -> None:
    gold = [_gold(0, 9, "123456782", EntityType.BSN)]
    pred = [
        _pred(0, 9, "123456782", EntityType.BSN),
        _pred(20, 25, "extra", EntityType.NAME),
    ]
    assert over_redaction_count(gold, pred) == 1
