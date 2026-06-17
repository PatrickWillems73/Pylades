"""Generalisatie-correctheid (BR-B01..B05) t.o.v. dataset-verwachtingen."""

from __future__ import annotations

from typing import Any

from eval.runners.base import PredEntity
from eval.schema import EvalRecord, GoldEntity
from proxy.generalization import GeneralizationConfig, generalize_all
from shared.models import DetectionLayer, Entity


def _gold_entities_for_original(record: EvalRecord, original: str) -> list[GoldEntity]:
    return [ent for ent in record.entities if ent.text == original]


def _match_entity_index(
    entities: list[Entity], original: str, golds: list[GoldEntity]
) -> int | None:
    """Koppel detectie aan gold; bij één gold-span prefer exacte offsets."""
    if len(golds) == 1:
        gold = golds[0]
        for idx, ent in enumerate(entities):
            if ent.original == original and ent.start == gold.start and ent.end == gold.end:
                return idx
    return next((idx for idx, ent in enumerate(entities) if ent.original == original), None)


def _preds_to_entities(preds: list[PredEntity]) -> list[Entity]:
    layer_map = {
        DetectionLayer.REGEX.value: DetectionLayer.REGEX,
        DetectionLayer.SPACY.value: DetectionLayer.SPACY,
        DetectionLayer.DEDUCE.value: DetectionLayer.DEDUCE,
        DetectionLayer.LLM.value: DetectionLayer.LLM,
    }
    entities: list[Entity] = []
    for pred in preds:
        layer = layer_map.get(pred.layer, DetectionLayer.DEDUCE)
        entities.append(
            Entity(
                original=pred.text,
                entity_type=pred.type,
                confidence=pred.confidence,
                detection_layer=layer,
                start=pred.start,
                end=pred.end,
            )
        )
    return entities


def generalization_failures(
    record: EvalRecord,
    preds: list[PredEntity],
    *,
    config: GeneralizationConfig | None = None,
) -> list[dict[str, Any]]:
    """Geef mislukte checks voor ``record.expected_generalization``."""
    if not record.expected_generalization:
        return []

    cfg = config or GeneralizationConfig()
    entities = _preds_to_entities(preds)
    gen_text, gen_entities = generalize_all(record.prompt, entities, cfg)

    failures: list[dict[str, Any]] = []
    for original, expected in record.expected_generalization.items():
        golds = _gold_entities_for_original(record, original)
        gold_type = golds[0].type if len(golds) == 1 else None
        source_idx = _match_entity_index(entities, original, golds)
        if source_idx is None:
            failures.append(
                {
                    "record": record.id,
                    "original": original,
                    "expected": expected,
                    "expected_type": gold_type.value if gold_type else None,
                    "reason": "entity_not_detected",
                }
            )
            continue
        detected = entities[source_idx]
        if gold_type is not None and detected.entity_type is not gold_type:
            failures.append(
                {
                    "record": record.id,
                    "original": original,
                    "expected": expected,
                    "expected_type": gold_type.value,
                    "detected_type": detected.entity_type.value,
                    "reason": "wrong_entity_type",
                }
            )
            continue
        gen_ent = gen_entities[source_idx]
        actual = gen_text[gen_ent.start : gen_ent.end]
        if expected not in actual:
            failures.append(
                {
                    "record": record.id,
                    "original": original,
                    "expected": expected,
                    "expected_type": gold_type.value if gold_type else None,
                    "detected_type": detected.entity_type.value,
                    "actual": actual,
                    "reason": "wrong_generalized_form",
                }
            )
    return failures


def format_generalization_summary(gen: dict[str, Any] | None) -> str:
    """Eén regel voor terminal/HTML/CSV: ``42/42 (100,0%)`` of ``0/0 (n.v.t.)``."""
    data = gen or {}
    checked = int(data.get("checked") or 0)
    ok = int(data.get("ok") or 0)
    if checked == 0:
        return "0/0 (n.v.t.)"
    rate = float(data.get("rate", ok / checked))
    pct = round(rate * 100, 1)
    # Nederlandse decimaal voor weergave (zelfde conventie als eval/report._nl_num).
    pct_str = f"{pct:,}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{ok}/{checked} ({pct_str}%)"
