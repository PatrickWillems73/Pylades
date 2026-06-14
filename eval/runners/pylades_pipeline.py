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
from eval.runners.outbound import build_outbound
from proxy.detection import Layer3Backend, Thresholds, detect_all_timed
from proxy.generalization import GeneralizationConfig
from shared.config import settings
from shared.models import DetectionLayer, Entity


class PyladesPipelineRunner:
    """Draait regex + DEDUCE (+ optioneel laag-3) zoals de proxy dat doet."""

    def __init__(
        self,
        *,
        name: str = "pylades_md",
        use_llm: bool = False,
        thresholds: Thresholds | None = None,
        llm_backend: Layer3Backend | None = None,
    ) -> None:
        self.name = name
        # Publiek zodat het rapport laag 2/3 kan benoemen in describe_layers().
        self.use_llm = use_llm
        self.layer2_desc = "deduce (NL-medisch + rol-NAME-heuristiek)"
        self.spacy_model = None
        self._llm_backend = llm_backend
        # Toon het model van de geïnjecteerde backend indien aanwezig (bv. MLX),
        # anders het Ollama-default dat detect_all gebruikt.
        self.llm_model = llm_backend.model if llm_backend is not None else settings.ollama_model
        self._thresholds = thresholds or Thresholds()
        self._gen_config = GeneralizationConfig()
        if llm_backend is not None:
            ensure = getattr(llm_backend, "ensure_available", None)
            if ensure is not None:
                ensure()

    def run(self, prompt: str) -> RunOutput:
        start = time.perf_counter()
        detection, timings = detect_all_timed(
            prompt,
            use_llm=self.use_llm,
            thresholds=self._thresholds,
            llm_backend=self._llm_backend,
        )
        latency_ms = (time.perf_counter() - start) * 1000.0
        llm_status = next(
            (t.status.value for t in timings if t.layer is DetectionLayer.LLM), None
        )

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
        outbound_text = build_outbound(prompt, all_detected, self._gen_config)

        return RunOutput(
            predicted=predicted,
            outbound_text=outbound_text,
            latency_ms=latency_ms,
            llm_status=llm_status,
        )


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
