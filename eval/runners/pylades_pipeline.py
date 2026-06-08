"""Runner die de echte Pylades-detectie-pijplijn draait.

Gebruikt expliciete `Thresholds()` en `GeneralizationConfig()` zodat het
harnas niet van de runtime-`config`-tabel (content.db) afhangt en volledig
deterministisch/offline draait. De outbound-tekst spiegelt de
pseudonimisering: gedetecteerde spans (na generalisatie) worden vervangen
door `[CODE]`-placeholders, net als `_substitute_pseudonyms` in
[proxy/pseudonymization.py](proxy/pseudonymization.py) zou doen.
"""

from __future__ import annotations

import time

from eval.runners.base import PredEntity, RunOutput
from proxy.detection import Thresholds, detect_all
from proxy.generalization import GeneralizationConfig, generalize_all
from shared.models import SHORT_TYPE_CODES, Entity


def _placeholder(entity: Entity) -> str:
    return f"[{SHORT_TYPE_CODES[entity.entity_type]}]"


def _substitute(text: str, entities: list[Entity]) -> str:
    """Vervang niet-overlappende entity-spans door placeholders (langste-first-veilig)."""
    ordered = sorted(entities, key=lambda e: e.start)
    parts: list[str] = []
    last = 0
    prev_end = -1
    for ent in ordered:
        if ent.start < prev_end:
            # Overlap zou tot dubbele vervanging leiden; sla de latere over.
            continue
        parts.append(text[last : ent.start])
        parts.append(_placeholder(ent))
        last = ent.end
        prev_end = ent.end
    parts.append(text[last:])
    return "".join(parts)


class PyladesPipelineRunner:
    """Draait regex + spaCy (+ optioneel laag-3) zoals de proxy dat doet."""

    def __init__(
        self,
        *,
        name: str = "pylades_md",
        use_llm: bool = False,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.name = name
        self._use_llm = use_llm
        self._thresholds = thresholds or Thresholds()
        self._gen_config = GeneralizationConfig()

    def run(self, prompt: str) -> RunOutput:
        start = time.perf_counter()
        detection = detect_all(prompt, use_llm=self._use_llm, thresholds=self._thresholds)
        latency_ms = (time.perf_counter() - start) * 1000.0

        predicted: list[PredEntity] = []
        for ent in detection.confident_entities:
            predicted.append(_pred_from_entity(ent, pending=False))
        for ent in detection.pending_review:
            predicted.append(_pred_from_entity(ent, pending=True))

        # Outbound = wat het externe LLM zou zien. Alle gedetecteerde entities
        # (confident + pending) worden, na generalisatie, weggemaskeerd: een
        # pending item blokkeert in productie de call (HTTP 423), dus de tekst
        # zou sowieso niet zo verzonden worden.
        all_detected = list(detection.confident_entities) + list(detection.pending_review)
        gen_text, gen_entities = generalize_all(prompt, all_detected, self._gen_config)
        outbound_text = _substitute(gen_text, gen_entities)

        return RunOutput(predicted=predicted, outbound_text=outbound_text, latency_ms=latency_ms)


def _pred_from_entity(ent: Entity, *, pending: bool) -> PredEntity:
    return PredEntity(
        start=ent.start,
        end=ent.end,
        text=ent.original,
        type=ent.entity_type,
        confidence=ent.confidence,
        layer=ent.detection_layer.value,
        pending_review=pending,
    )
