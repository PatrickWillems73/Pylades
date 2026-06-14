"""DEDUCE als enige runtime laag 2 (+ rol-heuristiek + NAME-span-uitbreiding).

Single source of truth voor de productie-pijplijn (`detect_all`) en het
eval-harnas (`DeduceBackend`). spaCy blijft alleen beschikbaar als
vergelijkings-backend in het eval-extra.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from proxy.name_spans import expand_name_span
from proxy.role_names import detect_role_context_name_spans
from shared.models import DetectionLayer, Entity, EntityType

logger = logging.getLogger(__name__)

_DEDUCE_TAGS: dict[str, EntityType] = {
    "patient": EntityType.NAME,
    "persoon": EntityType.NAME,
    "locatie": EntityType.LOCATION,
    "instelling": EntityType.ORG,
    "ziekenhuis": EntityType.ORG,
}

_DEDUCE_CONFIDENCE = 1.0
_ROLE_CONFIDENCE = 0.85


def _deduce_base_tag(tag: str) -> str:
    return tag.split("+", maxsplit=1)[0].split("_", maxsplit=1)[0].strip().lower()


@lru_cache(maxsize=1)
def _get_deduce() -> Any:
    from deduce import Deduce  # noqa: PLC0415

    return Deduce()


def deduce_available() -> bool:
    try:
        import deduce  # noqa: F401, PLC0415
    except ImportError:
        return False
    try:
        _get_deduce()
    except Exception:  # noqa: BLE001
        return False
    return True


def _span_overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _expand_name_entities(text: str, entities: list[Entity]) -> list[Entity]:
    expanded: list[Entity] = []
    for ent in entities:
        if ent.entity_type is not EntityType.NAME:
            expanded.append(ent)
            continue
        new_start, new_end = expand_name_span(text, ent.start, ent.end)
        if new_start == ent.start and new_end == ent.end:
            expanded.append(ent)
            continue
        expanded.append(
            ent.model_copy(
                update={
                    "start": new_start,
                    "end": new_end,
                    "original": text[new_start:new_end],
                }
            )
        )
    return expanded


def _merge_name_entities(merged: list[Entity], additions: list[Entity]) -> list[Entity]:
    if not additions:
        return merged
    result = list(merged)
    for add in additions:
        if add.entity_type is not EntityType.NAME:
            continue
        add_box = (add.start, add.end)
        overlap_idxs = [
            idx
            for idx, existing in enumerate(result)
            if existing.entity_type is EntityType.NAME
            and _span_overlaps(add_box, (existing.start, existing.end))
        ]
        if not overlap_idxs:
            result.append(add)
            continue
        best_existing = max(
            (result[idx] for idx in overlap_idxs),
            key=lambda ent: (ent.end - ent.start, ent.end),
        )
        if (add.end - add.start) <= (best_existing.end - best_existing.start):
            continue
        for idx in sorted(overlap_idxs, reverse=True):
            del result[idx]
        result.append(add)
    return result


def _coalesce_name_entities(entities: list[Entity]) -> list[Entity]:
    names = [ent for ent in entities if ent.entity_type is EntityType.NAME]
    if len(names) <= 1:
        return entities
    others = [ent for ent in entities if ent.entity_type is not EntityType.NAME]
    kept: list[Entity] = []
    for ent in sorted(names, key=lambda e: (e.end - e.start, e.end), reverse=True):
        if any(_span_overlaps((ent.start, ent.end), (k.start, k.end)) for k in kept):
            continue
        kept.append(ent)
    return others + kept


def _role_context_entities(text: str) -> list[Entity]:
    return [
        Entity(
            original=surface,
            entity_type=EntityType.NAME,
            confidence=_ROLE_CONFIDENCE,
            detection_layer=DetectionLayer.DEDUCE,
            start=start,
            end=end,
        )
        for start, end, surface in detect_role_context_name_spans(text)
    ]


def _raw_deduce_entities(text: str) -> list[Entity]:
    doc = _get_deduce().deidentify(text)
    entities: list[Entity] = []
    for ann in doc.annotations:
        etype = _DEDUCE_TAGS.get(_deduce_base_tag(str(ann.tag)))
        if etype is None:
            continue
        entities.append(
            Entity(
                original=ann.text,
                entity_type=etype,
                confidence=_DEDUCE_CONFIDENCE,
                detection_layer=DetectionLayer.DEDUCE,
                start=ann.start_char,
                end=ann.end_char,
            )
        )
    return entities


def supplement_deduce_name_entities(
    text: str, entities: list[Entity], additions: list[Entity]
) -> list[Entity]:
    """Voeg extra NAME-entities toe (eval GLiNER-fallback)."""
    entities = _merge_name_entities(entities, additions)
    entities = _expand_name_entities(text, entities)
    return _coalesce_name_entities(entities)


def detect_deduce_entities(
    text: str,
    *,
    supplement_names: Callable[[str, list[Entity]], list[Entity]] | None = None,
) -> list[Entity]:
    """DEDUCE + rol-heuristiek + NAME-uitbreiding; optionele NAME-supplement (eval)."""
    entities = _raw_deduce_entities(text)
    entities = _merge_name_entities(entities, _role_context_entities(text))
    entities = _expand_name_entities(text, entities)
    if supplement_names is not None:
        entities = supplement_names(text, entities)
        entities = _expand_name_entities(text, entities)
    return _coalesce_name_entities(entities)
