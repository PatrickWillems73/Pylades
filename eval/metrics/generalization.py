"""Generalisatie-correctheid (BR-B01..B05) t.o.v. dataset-verwachtingen."""

from __future__ import annotations

from typing import Any

from eval.runners.base import PredEntity
from eval.schema import EvalRecord
from proxy.generalization import GeneralizationConfig, generalize_all
from shared.models import DetectionLayer, Entity


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
        source_idx = next((i for i, ent in enumerate(entities) if ent.original == original), None)
        if source_idx is None:
            failures.append(
                {
                    "record": record.id,
                    "original": original,
                    "expected": expected,
                    "reason": "entity_not_detected",
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
                    "reason": "wrong_generalized_form",
                }
            )
    return failures
