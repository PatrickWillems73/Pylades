"""Runner voor laag-2 NER-modelvergelijking (TESTPLAN.md fase 3).

Draait dezelfde pijplijn als de Pylades-baseline — regex-laag 1 + outbound-
maskering (generalisatie + placeholders) — maar met een **verwisselbare**
laag-2 NER-backend (spaCy lg/trf, GLiNER, DEDUCE). Zo meet je per model
dezelfde PRF/lek-/latency-metrics en is het verschil puur het NER-model.

De regex-laag wint bij overlap (deterministisch, "earlier-layer-wins" zoals
[proxy/detection.py](proxy/detection.py)); NER-spans die niet met regex of met
elkaar overlappen worden toegevoegd. Laag 3 (LLM) draait hier niet mee: dit
harnas vergelijkt laag 2, niet de optionele product/projectdetectie.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from eval.runners.base import PredEntity, RunOutput
from eval.runners.ner_backends import Ner2Backend, NerSpan
from eval.runners.outbound import build_outbound
from proxy.detection import detect_regex
from proxy.generalization import GeneralizationConfig
from shared.models import DetectionLayer, Entity


@dataclass(frozen=True)
class _Merged:
    """Eén overlevende detectie plus het laag-label voor het rapport."""

    entity: Entity
    layer: str


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


class NerPipelineRunner:
    """regex (laag 1) + een verwisselbare laag-2 NER-backend."""

    use_llm = False
    llm_model = None

    def __init__(self, *, name: str, backend: Ner2Backend) -> None:
        self.name = name
        self._backend = backend
        # Het rapport benoemt laag 2 via `layer2_desc`; `spacy_model` blijft
        # gevuld voor spaCy-backends zodat de per-type "laag"-kolom "spacy lg"
        # toont (None voor gliner/deduce, die een eigen laag-label hebben).
        self.layer2_desc = backend.desc
        self.spacy_model = (
            getattr(backend, "model_name", None) if backend.layer == "spacy" else None
        )
        self._gen_config = GeneralizationConfig()
        backend.ensure_available()

    def run(self, prompt: str) -> RunOutput:
        start = time.perf_counter()

        merged: list[_Merged] = []
        taken: list[tuple[int, int]] = []

        for ent in detect_regex(prompt):
            merged.append(_Merged(ent, DetectionLayer.REGEX.value))
            taken.append((ent.start, ent.end))

        for span in self._backend.detect(prompt):
            if any(_overlap((span.start, span.end), t) for t in taken):
                continue
            merged.append(_Merged(_entity_from_span(span, self._backend.layer), self._backend.layer))
            taken.append((span.start, span.end))

        latency_ms = (time.perf_counter() - start) * 1000.0

        predicted = [
            PredEntity(
                start=m.entity.start,
                end=m.entity.end,
                text=m.entity.original,
                type=m.entity.entity_type,
                confidence=m.entity.confidence,
                layer=m.layer,
            )
            for m in merged
        ]
        outbound_text = build_outbound(prompt, [m.entity for m in merged], self._gen_config)

        return RunOutput(
            predicted=predicted,
            outbound_text=outbound_text,
            latency_ms=latency_ms,
            llm_status="disabled",
        )


def _entity_from_span(span: NerSpan, layer: str) -> Entity:
    detection_layer = {
        "spacy": DetectionLayer.SPACY,
        "deduce": DetectionLayer.DEDUCE,
    }.get(layer, DetectionLayer.DEDUCE)
    return Entity(
        original=span.text,
        entity_type=span.type,
        confidence=max(0.0, min(1.0, span.score)),
        detection_layer=detection_layer,
        start=span.start,
        end=span.end,
    )
