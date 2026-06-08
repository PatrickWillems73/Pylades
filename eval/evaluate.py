"""Orkestreer een evaluatie: dataset + runner → serialiseerbaar rapport.

Het rapport is een platte dict (JSON-vriendelijk) met per matching-modus
(exact/overlap) de PRF per `EntityType` plus micro/macro, een verwarringsmatrix,
de lek-analyse (primaire privacy-KPI), over-redactie en latency-percentielen.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from eval.metrics.scoring import (
    Counts,
    Leak,
    confusion_pairs,
    counts_by_type,
    find_exposed,
    match_entities,
    over_redaction_count,
    precision_recall_f1,
)
from eval.runners.base import Runner
from eval.schema import EvalRecord
from shared.models import EntityCategory, EntityType

# Categorieën waarvoor we blootstelling rapporteren; alleen direct-identifiers
# vormen de harde gate (zie TESTPLAN.md §6).
_EXPOSURE_CATEGORIES = (
    EntityCategory.DIRECT_IDENTIFIER,
    EntityCategory.CLINICAL_SENSITIVE,
    EntityCategory.QUASI_IDENTIFIER,
)

_MODES = ("exact", "overlap")


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _score_block(per_type: dict[EntityType, Counts]) -> dict[str, Any]:
    by_type: dict[str, Any] = {}
    micro = Counts()
    f1s: list[float] = []
    for etype, counts in sorted(per_type.items(), key=lambda kv: kv[0].value):
        p, r, f1 = precision_recall_f1(counts)
        by_type[etype.value] = {
            "tp": counts.tp,
            "fp": counts.fp,
            "fn": counts.fn,
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
        }
        micro.add(counts)
        # Macro telt alleen types die in de gold voorkomen (tp+fn > 0).
        if counts.tp + counts.fn > 0:
            f1s.append(f1)
    mp, mr, mf1 = precision_recall_f1(micro)
    return {
        "by_type": by_type,
        "micro": {
            "tp": micro.tp,
            "fp": micro.fp,
            "fn": micro.fn,
            "precision": round(mp, 4),
            "recall": round(mr, 4),
            "f1": round(mf1, 4),
        },
        "macro_f1": round(mean(f1s), 4) if f1s else 0.0,
    }


def evaluate(records: list[EvalRecord], runner: Runner) -> dict[str, Any]:
    per_type: dict[str, dict[EntityType, Counts]] = {m: defaultdict(Counts) for m in _MODES}
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    latencies: list[float] = []
    over_redaction = 0

    category_totals: dict[EntityCategory, int] = defaultdict(int)
    exposure_items: dict[EntityCategory, list[dict[str, Any]]] = {
        cat: [] for cat in _EXPOSURE_CATEGORIES
    }
    per_record: list[dict[str, Any]] = []

    for record in records:
        out = runner.run(record.prompt)
        latencies.append(out.latency_ms)

        for mode in _MODES:
            result = match_entities(record.entities, out.predicted, mode)
            for etype, counts in counts_by_type(record.entities, out.predicted, result).items():
                per_type[mode][etype].add(counts)
            if mode == "overlap":
                for g_type, p_type in confusion_pairs(record.entities, out.predicted, result):
                    confusion[g_type.value][p_type.value] += 1

        over_redaction += over_redaction_count(record.entities, out.predicted)

        for ent in record.entities:
            if ent.category in exposure_items:
                category_totals[ent.category] += 1

        record_leaks: list[Leak] = []
        for category in _EXPOSURE_CATEGORIES:
            for leak in find_exposed(record, out.predicted, category):
                record_leaks.append(leak)
                exposure_items[category].append(
                    {
                        "record": record.id,
                        "type": leak.entity.type.value,
                        "text": leak.entity.text,
                        "coverage": round(leak.coverage, 4),
                        "severity": leak.severity,
                    }
                )

        per_record.append(
            {
                "id": record.id,
                "difficulty": record.difficulty,
                "gold": len(record.entities),
                "pred": len(out.predicted),
                "leaks": [leak.entity.text for leak in record_leaks],
                "latency_ms": round(out.latency_ms, 2),
            }
        )

    direct_total = category_totals[EntityCategory.DIRECT_IDENTIFIER]
    direct_items = exposure_items[EntityCategory.DIRECT_IDENTIFIER]
    leak_rate = (len(direct_items) / direct_total) if direct_total else 0.0

    exposure_block: dict[str, Any] = {}
    for category in _EXPOSURE_CATEGORIES:
        total = category_totals[category]
        items = exposure_items[category]
        exposure_block[category.value] = {
            "total": total,
            "exposed": len(items),
            "rate": round(len(items) / total, 4) if total else 0.0,
            "items": items,
        }

    return {
        "runner": runner.name,
        "totals": {
            "records": len(records),
            "gold_entities": sum(len(r.entities) for r in records),
            "direct_identifiers": direct_total,
        },
        "scores": {mode: _score_block(per_type[mode]) for mode in _MODES},
        "confusion": {g: dict(preds) for g, preds in confusion.items()},
        "leaks": {
            "direct_total": direct_total,
            "direct_leaked": len(direct_items),
            "leak_rate": round(leak_rate, 4),
            "items": direct_items,
        },
        "exposure": exposure_block,
        "over_redaction": over_redaction,
        "latency": {
            "mean_ms": round(mean(latencies), 2) if latencies else 0.0,
            "p50_ms": round(_percentile(latencies, 0.50), 2),
            "p95_ms": round(_percentile(latencies, 0.95), 2),
        },
        "per_record": per_record,
    }


def all_entity_types() -> list[str]:
    return [t.value for t in EntityType]
