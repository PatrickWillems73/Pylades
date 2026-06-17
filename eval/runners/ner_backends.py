"""Laag-2 NER-backends voor modelvergelijking (TESTPLAN.md §7, fase 3).

Elke backend is een **adapter** rond een extern NER-model dat — naast de vaste
regex-laag 1 — laag 2 invult. Voor een eerlijke vergelijking richten alle
backends zich op dezelfde doeltypen die spaCy ook dekt: `NAME`, `ORG` en
`LOCATION`. Gestructureerde PII (BSN, IBAN, datums, …) blijft van de
deterministische regex-laag, die bij overlap wint; zo isoleren we puur de
NER-kwaliteit op vrije-tekst-entiteiten.

De zware modellen (GLiNER, DEDUCE) zitten in de optionele
`eval`-extra ([pyproject.toml](pyproject.toml)) en worden **lazy** geladen:
de import en eventuele modeldownload gebeuren pas in `ensure_available()`,
met een duidelijke installatie-hint bij ontbreken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from proxy.detection import _SPACY_LABEL_CONFIDENCE, _SPACY_LABEL_TO_TYPE
from proxy.deduce_layer import (
    deduce_available,
    detect_deduce_entities,
    supplement_deduce_name_entities,
)
from proxy.name_spans import expand_name_span
from shared.models import DetectionLayer, Entity, EntityType


class NerBackendError(RuntimeError):
    """Backend niet beschikbaar (ontbrekende dependency of model)."""


@dataclass(frozen=True)
class NerSpan:
    """Eén laag-2-detectie vóór merge met de regex-laag."""

    start: int
    end: int
    text: str
    type: EntityType
    score: float


@runtime_checkable
class Ner2Backend(Protocol):
    """Protocol voor een verwisselbare laag-2 NER-implementatie."""

    name: str
    layer: str
    desc: str

    def ensure_available(self) -> None: ...

    def detect(self, text: str) -> list[NerSpan]: ...


def _expand_name_spans(text: str, spans: list[NerSpan]) -> list[NerSpan]:
    """Breid NAME-detecties uit (tussenvoegsels, aanspreekvormen, apostrofnamen)."""
    expanded: list[NerSpan] = []
    for span in spans:
        if span.type is not EntityType.NAME:
            expanded.append(span)
            continue
        new_start, new_end = expand_name_span(text, span.start, span.end)
        if new_start == span.start and new_end == span.end:
            expanded.append(span)
            continue
        expanded.append(
            NerSpan(
                start=new_start,
                end=new_end,
                text=text[new_start:new_end],
                type=span.type,
                score=span.score,
            )
        )
    return expanded


# ---------------------------------------------------------------------------
# spaCy (md / lg) — eval-vergelijking; deelt labelmapping met detection.py
# ---------------------------------------------------------------------------


class SpacyNerBackend:
    """spaCy NER-model (`nl_core_news_md|lg`) als laag 2.

    Hergebruikt `_SPACY_LABEL_TO_TYPE`/`_SPACY_LABEL_CONFIDENCE` uit
    [proxy/detection.py](proxy/detection.py) zodat lg identiek aan de
    runtime-md gemapt wordt en de vergelijking alleen het model varieert.

    Explosion publiceert voor Nederlands géén `nl_core_news_trf` (alleen
    sm/md/lg); de transformer-rol in de benchmark is GLiNER (§TESTPLAN.md §7).
    """

    layer = "spacy"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.name = f"spacy_{model_name.rsplit('_', maxsplit=1)[-1]}"
        self.desc = f"spacy ({model_name})"
        self._nlp: Any | None = None

    def ensure_available(self) -> None:
        if self._nlp is not None:
            return
        try:
            import spacy  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - spaCy is eval-extra
            raise NerBackendError(f"spaCy ontbreekt: {exc}") from exc
        try:
            self._nlp = spacy.load(self.model_name)
        except OSError as exc:
            hint = (
                f"Installeer met:\n    uv run python -m spacy download {self.model_name}"
            )
            if self.model_name.endswith("_trf"):
                hint = (
                    "Explosion publiceert geen Nederlands transformer-spaCy-model "
                    f"({self.model_name!r}). Zie https://spacy.io/models/nl — alleen "
                    "sm/md/lg. Gebruik runner pylades_gliner voor de transformer-benchmark."
                )
            raise NerBackendError(
                f"spaCy-model {self.model_name!r} niet beschikbaar.\n{hint}"
            ) from exc

    def detect(self, text: str) -> list[NerSpan]:
        self.ensure_available()
        assert self._nlp is not None
        spans: list[NerSpan] = []
        for ent in self._nlp(text).ents:
            etype = _SPACY_LABEL_TO_TYPE.get(ent.label_)
            if etype is None:
                continue
            spans.append(
                NerSpan(
                    start=ent.start_char,
                    end=ent.end_char,
                    text=ent.text,
                    type=etype,
                    score=_SPACY_LABEL_CONFIDENCE.get(ent.label_, 0.85),
                )
            )
        return _expand_name_spans(text, spans)


# ---------------------------------------------------------------------------
# GLiNER — zero-shot transformer met echte confidences
# ---------------------------------------------------------------------------

# GLiNER-promptlabels → Pylades-type. Bewust beperkt tot de spaCy-doeltypen
# zodat de vergelijking apples-to-apples blijft (regex dekt de rest).
_GLINER_LABELS: dict[str, EntityType] = {
    "person": EntityType.NAME,
    "organization": EntityType.ORG,
    "location": EntityType.LOCATION,
}

# Standaard 768 subword-tokens: dekt de synthetische set (tot ~538 tokens) zonder
# truncatie; default model-max is 384. Bij langere teksten: overlappende chunks.
_GLINER_DEFAULT_MAX_LENGTH = 768
_GLINER_CHARS_PER_TOKEN = 3.5  # conservatief voor NL-dossiertekst
_GLINER_CHUNK_OVERLAP_CHARS = 400


def _gliner_single_pass_char_limit(max_length: int) -> int:
    """Ruwe tekengrens waaronder één GLiNER-pass past binnen `max_length` tokens."""
    return int(max_length * _GLINER_CHARS_PER_TOKEN * 0.95)


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _gliner_windows(text: str, *, limit: int, overlap: int) -> list[tuple[int, str]]:
    """Overlappende tekstvensters voor GLiNER op lange dossiers."""
    if len(text) <= limit:
        return [(0, text)]
    overlap = min(overlap, limit - 1)
    stride = max(1, limit - overlap)
    windows: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        windows.append((start, text[start:end]))
        if end >= len(text):
            break
        start += stride
    return windows


def _merge_ner_spans(spans: list[NerSpan]) -> list[NerSpan]:
    """Voeg overlappende chunk-detecties samen; hoogste score wint."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: (s.start, s.end))
    merged: list[NerSpan] = []
    for span in ordered:
        replaced = False
        for idx, kept in enumerate(merged):
            if _overlap((span.start, span.end), (kept.start, kept.end)):
                if span.score > kept.score:
                    merged[idx] = span
                replaced = True
                break
        if not replaced:
            merged.append(span)
    return merged


def _gliner_raw_to_spans(text: str, raw: list[dict]) -> list[NerSpan]:
    spans: list[NerSpan] = []
    for item in raw:
        etype = _GLINER_LABELS.get(str(item.get("label", "")).lower())
        if etype is None:
            continue
        try:
            start, end = int(item["start"]), int(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        spans.append(
            NerSpan(
                start=start,
                end=end,
                text=item.get("text", text[start:end]),
                type=etype,
                score=float(item.get("score", 0.5)),
            )
        )
    return spans


class GlinerBackend:
    """GLiNER (multilingual PII) als laag 2; lazy load + download."""

    layer = "gliner"

    def __init__(
        self,
        model_name: str = "urchade/gliner_multi_pii-v1",
        *,
        threshold: float = 0.5,
        max_length: int = _GLINER_DEFAULT_MAX_LENGTH,
        chunk_overlap_chars: int = _GLINER_CHUNK_OVERLAP_CHARS,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._max_length = max_length
        self._chunk_overlap_chars = chunk_overlap_chars
        self.name = "gliner"
        self.desc = f"gliner ({model_name}, max_len={max_length})"
        self._labels = list(_GLINER_LABELS)
        self._model: Any | None = None

    def ensure_available(self) -> None:
        if self._model is not None:
            return
        try:
            from gliner import GLiNER  # noqa: PLC0415
        except ImportError as exc:
            raise NerBackendError(
                "GLiNER ontbreekt. Installeer de eval-extra:\n    uv sync --extra eval"
            ) from exc
        try:
            self._model = GLiNER.from_pretrained(
                self.model_name, max_length=self._max_length
            )
        except Exception as exc:  # noqa: BLE001 - download/laad-fouten generiek melden
            raise NerBackendError(
                f"GLiNER-model {self.model_name!r} laden mislukte: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    def _predict_raw(self, chunk: str) -> list[dict]:
        assert self._model is not None
        return self._model.predict_entities(
            chunk, self._labels, threshold=self.threshold
        )

    def detect(self, text: str) -> list[NerSpan]:
        self.ensure_available()
        char_limit = _gliner_single_pass_char_limit(self._max_length)
        windows = _gliner_windows(
            text, limit=char_limit, overlap=self._chunk_overlap_chars
        )
        spans: list[NerSpan] = []
        for offset, chunk in windows:
            for span in _gliner_raw_to_spans(chunk, self._predict_raw(chunk)):
                spans.append(
                    NerSpan(
                        start=span.start + offset,
                        end=span.end + offset,
                        text=text[span.start + offset : span.end + offset],
                        type=span.type,
                        score=span.score,
                    )
                )
        return _merge_ner_spans(spans)


# ---------------------------------------------------------------------------
# DEDUCE — rule-based NL-medische de-identificatie (deelt proxy/deduce_layer)
# ---------------------------------------------------------------------------


def _entity_to_ner_span(ent: Entity) -> NerSpan:
    return NerSpan(
        start=ent.start,
        end=ent.end,
        text=ent.original,
        type=ent.entity_type,
        score=ent.confidence,
    )


class DeduceBackend:
    """DEDUCE 3.x als laag 2; rule-based + rol-heuristiek (+ optionele GLiNER-fallback)."""

    layer = "deduce"
    name = "deduce"
    desc = "deduce (NL-medisch + rol-NAME-heuristiek)"

    def __init__(self, *, name_fallback: bool = False) -> None:
        self._name_fallback = name_fallback
        self._gliner: GlinerBackend | None = None

    def ensure_available(self) -> None:
        if deduce_available():
            return
        try:
            import deduce  # noqa: F401, PLC0415
        except ImportError as exc:
            raise NerBackendError(
                "DEDUCE ontbreekt. Installeer de hoofd-dependencies:\n    uv sync"
            ) from exc
        raise NerBackendError("DEDUCE initialiseren mislukte (zie logs)")

    def _gliner_fallback(self) -> GlinerBackend:
        if self._gliner is None:
            self._gliner = GlinerBackend()
            self._gliner.ensure_available()
        return self._gliner

    def detect(self, text: str) -> list[NerSpan]:
        self.ensure_available()
        if self._name_fallback:
            gliner = self._gliner_fallback()

            def supplement(t: str, entities: list[Entity]) -> list[Entity]:
                additions = [
                    Entity(
                        original=span.text,
                        entity_type=span.type,
                        confidence=span.score,
                        detection_layer=DetectionLayer.DEDUCE,
                        start=span.start,
                        end=span.end,
                    )
                    for span in gliner.detect(t)
                    if span.type is EntityType.NAME
                ]
                return supplement_deduce_name_entities(t, entities, additions)

            entities = detect_deduce_entities(text, supplement_names=supplement)
        else:
            entities = detect_deduce_entities(text)
        return [_entity_to_ner_span(ent) for ent in entities]
