"""Span/type-scoring, verwarringsmatrix en lek-detectie.

Matching gebeurt greedy en één-op-één per type:
- `exact`: identieke offsets (`start` en `end`) én type.
- `overlap`: zelfde type en overlappende span (grootste overlap wint).

De verwarringsmatrix wordt afgeleid uit de *resterende* (ongematchte) gold- en
pred-entities: overlappen ze type-agnostisch, dan is dat een type-verwisseling.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from eval.runners.base import PredEntity
from eval.schema import EvalRecord, GoldEntity
from shared.models import EntityCategory, EntityType


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: Counts) -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn


def precision_recall_f1(c: Counts) -> tuple[float, float, float]:
    precision = c.tp / (c.tp + c.fp) if (c.tp + c.fp) else 0.0
    recall = c.tp / (c.tp + c.fn) if (c.tp + c.fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def _overlap_len(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


@dataclass
class MatchResult:
    matched: list[tuple[int, int]] = field(default_factory=list)  # (gold_idx, pred_idx)
    unmatched_gold: list[int] = field(default_factory=list)
    unmatched_pred: list[int] = field(default_factory=list)


def match_entities(
    gold: list[GoldEntity],
    pred: list[PredEntity],
    mode: str,
) -> MatchResult:
    used_pred: set[int] = set()
    used_gold: set[int] = set()
    matched: list[tuple[int, int]] = []

    for gi, g in enumerate(gold):
        best_pi: int | None = None
        best_ov = -1
        for pi, p in enumerate(pred):
            if pi in used_pred or p.type is not g.type:
                continue
            if mode == "exact":
                if p.start == g.start and p.end == g.end:
                    best_pi = pi
                    break
            else:  # overlap
                ov = _overlap_len(g.start, g.end, p.start, p.end)
                if ov > 0 and ov > best_ov:
                    best_pi = pi
                    best_ov = ov
        if best_pi is not None:
            used_pred.add(best_pi)
            used_gold.add(gi)
            matched.append((gi, best_pi))

    return MatchResult(
        matched=matched,
        unmatched_gold=[gi for gi in range(len(gold)) if gi not in used_gold],
        unmatched_pred=[pi for pi in range(len(pred)) if pi not in used_pred],
    )


def counts_by_type(
    gold: list[GoldEntity],
    pred: list[PredEntity],
    result: MatchResult,
) -> dict[EntityType, Counts]:
    counts: dict[EntityType, Counts] = defaultdict(Counts)
    for gi, _pi in result.matched:
        counts[gold[gi].type].tp += 1
    for pi in result.unmatched_pred:
        counts[pred[pi].type].fp += 1
    for gi in result.unmatched_gold:
        counts[gold[gi].type].fn += 1
    return counts


def confusion_pairs(
    gold: list[GoldEntity],
    pred: list[PredEntity],
    result: MatchResult,
) -> list[tuple[EntityType, EntityType]]:
    """Type-agnostische overlap tussen ongematchte gold/pred → (gold_type, pred_type)."""
    pairs: list[tuple[EntityType, EntityType]] = []
    used_pred: set[int] = set()
    for gi in result.unmatched_gold:
        g = gold[gi]
        best_pi: int | None = None
        best_ov = 0
        for pi in result.unmatched_pred:
            if pi in used_pred:
                continue
            p = pred[pi]
            ov = _overlap_len(g.start, g.end, p.start, p.end)
            if ov > best_ov:
                best_ov = ov
                best_pi = pi
        if best_pi is not None:
            used_pred.add(best_pi)
            pairs.append((g.type, pred[best_pi].type))
    return pairs


def _coverage_fraction(start: int, end: int, pred: list[PredEntity]) -> float:
    """Welk deel van [start, end) wordt door (samengevoegde) pred-spans gedekt.

    Span-gebaseerd in originele coördinaten: of een entity vervolgens
    one-way-gepseudonimiseerd of gegeneraliseerd wordt maakt niet uit — in
    beide gevallen verdwijnt het origineel. Een gedetecteerde span met een
    *ander* type (bv. ADDRESS die als LOCATION gemaskeerd wordt) telt dus ook
    als bescherming; de type-fout zelf zit in de PRF/verwarringsmatrix.
    """
    span_len = end - start
    if span_len <= 0:
        return 1.0
    intervals = sorted(
        (max(start, p.start), min(end, p.end))
        for p in pred
        if min(end, p.end) > max(start, p.start)
    )
    if not intervals:
        return 0.0
    covered = 0
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s > cur_e:
            covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    covered += cur_e - cur_s
    return covered / span_len


@dataclass
class Leak:
    entity: GoldEntity
    coverage: float

    @property
    def severity(self) -> str:
        return "full" if self.coverage == 0.0 else "partial"


def find_exposed(
    record: EvalRecord,
    predicted: list[PredEntity],
    category: EntityCategory,
) -> list[Leak]:
    """Entities van `category` die niet volledig door detectie-spans gedekt worden.

    Privacy-first: alles onder 100% dekking telt mee (een deels-blootgesteld
    identifier blijft blootgesteld). Gegeneraliseerde entities (geboortedatum,
    PC6) zijn wél gedetecteerd en dus volledig gedekt.
    """
    leaks: list[Leak] = []
    for ent in record.entities:
        if ent.category is not category:
            continue
        coverage = _coverage_fraction(ent.start, ent.end, predicted)
        if coverage < 1.0:
            leaks.append(Leak(entity=ent, coverage=coverage))
    return leaks


def find_leaks(record: EvalRecord, predicted: list[PredEntity]) -> list[Leak]:
    """Direct-identifiers die niet volledig gedekt worden (primaire privacy-KPI)."""
    return find_exposed(record, predicted, EntityCategory.DIRECT_IDENTIFIER)


def over_redaction_count(
    gold: list[GoldEntity],
    pred: list[PredEntity],
) -> int:
    """Voorspelde spans die geen enkele gold-entity (type-agnostisch) overlappen."""
    count = 0
    for p in pred:
        if not any(_overlap_len(g.start, g.end, p.start, p.end) > 0 for g in gold):
            count += 1
    return count
