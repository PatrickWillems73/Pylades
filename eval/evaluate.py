"""Orkestreer een evaluatie: dataset + runner → serialiseerbaar rapport.

Het rapport is een platte dict (JSON-vriendelijk) met per matching-modus
(exact/overlap) de PRF per `EntityType` plus micro/macro, een verwarringsmatrix,
de lek-analyse (primaire privacy-KPI), over-redactie en latency-percentielen.

**Warm-up.** De eerste runner-aanroep betaalt eenmalige cold-start-kosten
(o.a. het lazy laden van DEDUCE, ~1-2 s) die niets met de
detectie-snelheid per dossier te maken hebben. Zonder correctie vertekent die
ene uitschieter p95 en mean, wat modellen oneerlijk vergelijkt. Daarom draait
`evaluate()` standaard eerst één **warm-up-aanroep** op een vaste dummy-prompt
waarvan de latency wordt weggegooid (maar wel apart in het rapport vermeld).
Zet `warmup=False` (CLI: `--no-warmup`) om de cold-start juist te meten.
"""

from __future__ import annotations

import platform
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from eval.metrics.generalization import format_generalization_summary, generalization_failures
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
from proxy.detection import detector_layers_by_type
from proxy.generalization import GeneralizationConfig
from shared.models import ENTITY_CATEGORY_MAP, EntityCategory, EntityType

# Categorieën waarvoor we blootstelling rapporteren; alleen direct-identifiers
# vormen de harde gate (zie TESTPLAN.md §6).
_EXPOSURE_CATEGORIES = (
    EntityCategory.DIRECT_IDENTIFIER,
    EntityCategory.CLINICAL_SENSITIVE,
    EntityCategory.QUASI_IDENTIFIER,
)

_MODES = ("exact", "overlap")

# Vaste dummy-prompt voor de warm-up. Bevat een naam (laag 2/DEDUCE), een
# datum en een BSN-achtig nummer (laag 1/regex) zodat dezelfde codepaden warm
# draaien als bij echte dossiers. De inhoud is fictief en doet niet mee in de
# metrics — alleen de modelcaches worden geïnitialiseerd.
_WARMUP_PROMPT = (
    "Warm-up: patiënt Jan de Vries, geboren op 01-01-1980, BSN 123456782, "
    "opgenomen in het OLVG te Amsterdam."
)


def _sysctl(key: str) -> str | None:
    """Lees één sysctl-waarde (macOS); None bij elke fout/niet-darwin."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", key], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    val = out.stdout.strip()
    return val or None


def collect_environment() -> dict[str, Any]:
    """Leg de machine-specificatie vast voor reproduceerbaarheid/FG-audit.

    Op macOS halen we model, chip en RAM uit sysctl (bv. "MacBookPro17,1 ·
    Apple M1 · 8 GB"); op andere platforms vallen we terug op `platform`.
    """
    cpu = model = mem_gb_str = None
    memory_gb: int | None = None
    if platform.system() == "Darwin":
        cpu = _sysctl("machdep.cpu.brand_string")
        model = _sysctl("hw.model")
        mem = _sysctl("hw.memsize")
        if mem and mem.isdigit():
            memory_gb = round(int(mem) / (1024**3))
            mem_gb_str = f"{memory_gb} GB"
    if not cpu:
        cpu = platform.processor() or None
    parts = [p for p in (model, cpu, mem_gb_str) if p]
    summary = " · ".join(parts) if parts else platform.platform()
    return {
        "summary": summary,
        "os": platform.platform(),
        "machine_model": model,
        "cpu": cpu,
        "arch": platform.machine(),
        "memory_gb": memory_gb,
        "python": platform.python_version(),
    }


def describe_layers(run_meta: dict[str, Any]) -> dict[str, str]:
    """Beschrijf per detectielaag welk model/techniek deze run gebruikte."""
    # Laag 2 kan een andere NER-backend zijn dan runtime-DEDUCE (fase 3: spaCy
    # lg, GLiNER, …); de runner levert dan een expliciete `layer2`-beschrijving.
    layer2 = run_meta.get("layer2")
    if not layer2:
        layer2 = "deduce (NL-medisch + rol-NAME-heuristiek)"
    if not run_meta.get("use_llm"):
        layer3 = "niet gedraaid (laag 3 uit)"
    else:
        model = run_meta.get("llm_model") or "onbekend"
        status = run_meta.get("llm_status")
        layer3 = model if status == "ok" else f"{model} (niet beschikbaar: {status})"
    return {
        "layer1": "regex",
        "layer2": layer2,
        "layer3": layer3,
    }


def _aggregate_llm_status(statuses: Counter[str | None]) -> str | None:
    """Vat de per-record laag-3-status samen: 'ok' wint, anders de dominante.

    None-waarden (runner rapporteert geen laag-3-status) tellen mee als kandidaat
    en geven None terug wanneer er geen echte status is.
    """
    if statuses.get("ok"):
        return "ok"
    real = Counter({k: v for k, v in statuses.items() if k is not None})
    if real:
        return real.most_common(1)[0][0]
    return None


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


def _macro_f1_for_group(
    overlap_by_type: dict[str, Any], *, direct: bool
) -> float:
    """Macro-F1 over overlap-scores voor direct- of indirect-identifier-types met gold."""
    f1s: list[float] = []
    for etype, scores in overlap_by_type.items():
        try:
            category = ENTITY_CATEGORY_MAP[EntityType(etype)]
            is_direct = category is EntityCategory.DIRECT_IDENTIFIER
        except (KeyError, ValueError):
            is_direct = False
        if is_direct != direct:
            continue
        if scores["tp"] + scores["fn"] > 0:
            f1s.append(scores["f1"])
    return round(mean(f1s), 4) if f1s else 0.0


def _direct_identifier_macro_f1(overlap_by_type: dict[str, Any]) -> float:
    """Macro-F1 over overlap-scores, alleen direct-identifier-types met gold."""
    return _macro_f1_for_group(overlap_by_type, direct=True)


def _indirect_identifier_macro_f1(overlap_by_type: dict[str, Any]) -> float:
    """Macro-F1 over overlap-scores, alleen indirect-identifier-types met gold."""
    return _macro_f1_for_group(overlap_by_type, direct=False)


@dataclass
class _Accumulators:
    """Gedeelde tellers die per record worden bijgewerkt (zie `_score_one_record`)."""

    per_type: dict[str, dict[EntityType, Counts]]
    confusion: dict[str, dict[str, int]]
    layer_counts: dict[EntityType, Counter[str]]
    category_totals: dict[EntityCategory, int]
    exposure_items: dict[EntityCategory, list[dict[str, Any]]]


def _score_one_record(
    record: EvalRecord, out: Any, acc: _Accumulators
) -> tuple[list[Leak], int]:
    """Werk de tellers bij voor één record; geef de lekken + over-redactie terug."""
    for pred in out.predicted:
        acc.layer_counts[pred.type][pred.layer] += 1

    for mode in _MODES:
        result = match_entities(record.entities, out.predicted, mode)
        for etype, counts in counts_by_type(record.entities, out.predicted, result).items():
            acc.per_type[mode][etype].add(counts)
        if mode == "overlap":
            for g_type, p_type in confusion_pairs(record.entities, out.predicted, result):
                acc.confusion[g_type.value][p_type.value] += 1

    for ent in record.entities:
        if ent.category in acc.exposure_items:
            acc.category_totals[ent.category] += 1

    record_leaks: list[Leak] = []
    for category in _EXPOSURE_CATEGORIES:
        for leak in find_exposed(record, out.predicted, category):
            record_leaks.append(leak)
            acc.exposure_items[category].append(
                {
                    "record": record.id,
                    "type": leak.entity.type.value,
                    "text": leak.entity.text,
                    "coverage": round(leak.coverage, 4),
                    "severity": leak.severity,
                }
            )

    return record_leaks, over_redaction_count(record.entities, out.predicted)


def evaluate(
    records: list[EvalRecord],
    runner: Runner,
    *,
    warmup: bool = True,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    # Cold-start-kosten (DEDUCE-init, spaCy-load bij eval-runners, e.d.) eerst
    # opvangen met een wegwerp-aanroep, zodat de gemeten latencies de
    # steady-state per dossier weerspiegelen.
    warmup_ms: float | None = None
    total = len(records)
    if warmup:
        if on_progress is not None:
            on_progress(0, total, "warm-up")
        warmup_out = runner.run(_WARMUP_PROMPT)
        warmup_ms = round(warmup_out.latency_ms, 2)

    per_type: dict[str, dict[EntityType, Counts]] = {m: defaultdict(Counts) for m in _MODES}
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Per entity-type bijhouden welke detectielaag (regex/deduce/llm) de
    # voorspellingen leverde — informatief voor het rapport en modelkeuze.
    layer_counts: dict[EntityType, Counter[str]] = defaultdict(Counter)
    latencies: list[float] = []
    over_redaction = 0
    gen_config = GeneralizationConfig()
    generalization_checked = 0
    generalization_failures_items: list[dict[str, Any]] = []

    category_totals: dict[EntityCategory, int] = defaultdict(int)
    exposure_items: dict[EntityCategory, list[dict[str, Any]]] = {
        cat: [] for cat in _EXPOSURE_CATEGORIES
    }
    per_record: list[dict[str, Any]] = []

    acc = _Accumulators(
        per_type=per_type,
        confusion=confusion,
        layer_counts=layer_counts,
        category_totals=category_totals,
        exposure_items=exposure_items,
    )
    llm_statuses: Counter[str | None] = Counter()
    for index, record in enumerate(records, start=1):
        if on_progress is not None:
            on_progress(index, total, record.id)
        out = runner.run(record.prompt)
        latencies.append(out.latency_ms)
        llm_statuses[out.llm_status] += 1

        record_leaks, redacted = _score_one_record(record, out, acc)
        over_redaction += redacted
        if record.expected_generalization:
            generalization_checked += len(record.expected_generalization)
            generalization_failures_items.extend(
                generalization_failures(record, out.predicted, config=gen_config)
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

    run_meta = {
        "use_llm": bool(getattr(runner, "use_llm", False)),
        "spacy_model": getattr(runner, "spacy_model", None),
        # Expliciete laag-2-beschrijving van NER-vergelijkingsrunners (fase 3);
        # None voor runtime-runners, dan valt describe_layers terug op DEDUCE.
        "layer2": getattr(runner, "layer2_desc", None),
        "llm_model": getattr(runner, "llm_model", None),
        # Werkelijke laag-3-status over de run: "ok" als laag 3 minstens één
        # keer succesvol draaide, anders de dominante foutstatus (bv.
        # "unavailable" als de backend niet bereikbaar was).
        "llm_status": _aggregate_llm_status(llm_statuses),
    }

    overlap_scores = {mode: _score_block(per_type[mode]) for mode in _MODES}
    latency_mean_ms = round(mean(latencies), 2) if latencies else 0.0
    generalization_ok = generalization_checked - len(generalization_failures_items)
    generalization_rate = (
        generalization_ok / generalization_checked if generalization_checked else 1.0
    )
    generalization_block = {
        "checked": generalization_checked,
        "ok": generalization_ok,
        "rate": round(generalization_rate, 4),
        "failures": generalization_failures_items,
    }
    generalization_block["summary"] = format_generalization_summary(generalization_block)

    return {
        "runner": runner.name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "environment": collect_environment(),
        "layers_config": describe_layers(run_meta),
        "totals": {
            "records": len(records),
            "gold_entities": sum(len(r.entities) for r in records),
            "direct_identifiers": direct_total,
        },
        "performance": {
            "direct_identifier_macro_f1": _direct_identifier_macro_f1(
                overlap_scores["overlap"]["by_type"]
            ),
            "indirect_identifier_macro_f1": _indirect_identifier_macro_f1(
                overlap_scores["overlap"]["by_type"]
            ),
            "latency_mean_ms": latency_mean_ms,
        },
        "scores": overlap_scores,
        "layers_by_type": {
            etype.value: dict(counter.most_common())
            for etype, counter in sorted(layer_counts.items(), key=lambda kv: kv[0].value)
        },
        # Totaal aantal voorspellingen per type (som over lagen) — de "p"-kolom
        # in het rapport; los van tp/fp omdat het de ruwe detectie-count is.
        "predicted_by_type": {
            etype.value: sum(counter.values())
            for etype, counter in sorted(layer_counts.items(), key=lambda kv: kv[0].value)
        },
        "detector_layers": {
            etype.value: [layer.value for layer in layers]
            for etype, layers in detector_layers_by_type().items()
        },
        "run_meta": run_meta,
        "confusion": {g: dict(preds) for g, preds in confusion.items()},
        "leaks": {
            "direct_total": direct_total,
            "direct_leaked": len(direct_items),
            "leak_rate": round(leak_rate, 4),
            "items": direct_items,
        },
        "exposure": exposure_block,
        "over_redaction": over_redaction,
        "generalization": generalization_block,
        "latency": {
            "mean_ms": latency_mean_ms,
            "p50_ms": round(_percentile(latencies, 0.50), 2),
            "p95_ms": round(_percentile(latencies, 0.95), 2),
            "warmup": warmup,
            "warmup_ms": warmup_ms,
        },
        "per_record": per_record,
    }


def all_entity_types() -> list[str]:
    return [t.value for t in EntityType]
