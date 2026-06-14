"""Drielagige entity-detectie: regex -> spaCy -> Ollama (laag 3 default uit).

Volgorde is bewust van hoge naar lage precisie: regex (deterministisch,
hoge specificiteit, validators voor BSN/IBAN), dan spaCy NER (breder maar
zachter), dan optioneel Ollama (jargon/productnamen). Latere lagen mogen
*toevoegen* aan eerdere lagen, nooit *overschrijven* — geclaimde spans
blijven van wie ze eerst pakte.

Configureerbare thresholds per laag (en per spaCy-label) routeren entities
naar `confident_entities` of `pending_review` (BR-A04). Een operator kan
de threshold tijdelijk hoger zetten om bepaalde detecties geforceerd door
de manual-review-queue te laten lopen.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from time import perf_counter
from typing import Any, Final, Protocol

from proxy.name_spans import expand_name_span
from shared.config import settings
from shared.crypto import validate_bsn_elfproef, validate_iban_checksum
from shared.db import get_config_value
from shared.models import DetectionLayer, DetectionResult, Entity, EntityType

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-laag timing/status (voor de UI-voortgangsindicator)
# ---------------------------------------------------------------------------


class LayerStatus(StrEnum):
    """Uitkomst van één detectielaag, los van het aantal gevonden entities."""

    RUNNING = "running"
    OK = "ok"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LayerTiming:
    """Timing + status van één detectielaag voor één detect-run."""

    layer: DetectionLayer
    status: LayerStatus
    duration_ms: float | None
    entity_count: int


# ---------------------------------------------------------------------------
# Regex-patronen (BR-A02)
# ---------------------------------------------------------------------------

# Context-date-patronen: groep 1 vangt alléén de datum, niet de aanleiding.
# Worden in een aparte pre-pass behandeld zodat ze hun datum-span claimen
# voordat het algemene BIRTHDATE-patroon eraan kan komen (BR-B04 vereist
# dat een opname-datum onderscheidbaar blijft van een geboortedatum).
_DATE_BODY = r"\d{1,2}[-/]\d{1,2}[-/]\d{4}"

CONTEXT_DATE_PATTERNS: Final[list[tuple[EntityType, re.Pattern[str]]]] = [
    (
        EntityType.ADMISSION_DATE,
        re.compile(
            rf"(?:opname|opgenomen|opnamedatum|opnemingsdatum)\D{{0,20}}({_DATE_BODY})",
            re.IGNORECASE,
        ),
    ),
    (
        EntityType.DISCHARGE_DATE,
        re.compile(
            rf"(?:ontslag|ontslagdatum|ontslagen)\D{{0,20}}({_DATE_BODY})",
            re.IGNORECASE,
        ),
    ),
    (
        EntityType.EXAM_DATE,
        re.compile(
            rf"(?:onderzoek|onderzoeksdatum|scan|mri|ct)\D{{0,20}}({_DATE_BODY})",
            re.IGNORECASE,
        ),
    ),
]


# Algemene regex-laag. Volgorde is significant: vroege entries claimen hun
# span eerst, zodat overlappende patronen (bv. EPD_ID vs PROJECT) consistent
# uitvallen voor de meer-specifieke variant.
REGEX_PATTERNS: Final[list[tuple[EntityType, re.Pattern[str], Callable[[str], bool] | None]]] = [
    (
        EntityType.EMAIL,
        re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
        None,
    ),
    (
        EntityType.IBAN,
        re.compile(r"\bNL\d{2}[A-Z]{4}\d{10}\b"),
        validate_iban_checksum,
    ),
    (
        EntityType.BSN,
        re.compile(r"\b\d{9}\b"),
        validate_bsn_elfproef,
    ),
    (
        EntityType.ADDRESS,
        re.compile(
            r"\b(?:[A-Z][\w-]+\s+)?[A-Za-z][\w-]*"
            r"(?:straat|weg|laan|plein|gracht|kade|singel|dreef|pad|hof|boulevard|steeg)"
            r"\s+\d{1,4}[A-Za-z]?\b",
            re.IGNORECASE,
        ),
        None,
    ),
    (
        EntityType.POSTCODE_PC6,
        re.compile(r"\b\d{4}\s?[A-Z]{2}\b"),
        None,
    ),
    (
        EntityType.PHONE,
        re.compile(r"\b(?:\+31|0)[\s-]?[1-9](?:[\s-]?\d){8}\b"),
        None,
    ),
    (
        EntityType.KENTEKEN,
        re.compile(r"\b[A-Z0-9]{1,3}-[A-Z0-9]{1,3}-[A-Z0-9]{1,3}\b"),
        None,
    ),
    (
        EntityType.MRN,
        re.compile(r"\bMRN[:\s-]{0,3}\d{6,10}\b", re.IGNORECASE),
        None,
    ),
    (
        EntityType.EPD_ID,
        re.compile(r"\bEPD[:\s-]{0,3}\d{6,12}\b", re.IGNORECASE),
        None,
    ),
    (
        EntityType.ICD10_CODE,
        re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,2})?\b"),
        None,
    ),
    (
        EntityType.PROJECT,
        re.compile(r"\b[A-Z]{2,5}-\d{2,6}\b"),
        None,
    ),
    (
        EntityType.AGE,
        re.compile(r"\b(\d{1,3})[\s-]?(?:jaar|jarige?)\b", re.IGNORECASE),
        None,
    ),
    # BIRTHDATE als laatste: contextpatronen hierboven hebben al hun spans
    # geclaimd; wat hier nog matched is een datum zonder contextwoord.
    (
        EntityType.BIRTHDATE,
        re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[-/](?:0?[1-9]|1[0-2])[-/](?:19|20)\d{2}\b"),
        None,
    ),
]


# ---------------------------------------------------------------------------
# spaCy NER (BR-A02 brede dekking voor NAME/ORG/LOCATION)
# ---------------------------------------------------------------------------

# spaCy's `nl_core_news_md` heeft historisch twee NER-label-schema's:
#   - klassiek (CoNLL-stijl, tot ~v3.5): PER, ORG, LOC, MISC.
#   - actueel (OntoNotes-stijl, vanaf de medium/large modellen op v3.6+):
#     PERSON, ORG, LOC, GPE, FAC, NORP, …
# We mappen beide schema's zodat een model-upgrade ons niet stilzwijgend
# alle NAME-detecties laat verliezen. GPE ("Geo-Political Entity", bv.
# steden/landen) en FAC ("Facility") vallen naar LOCATION; alle overige
# OntoNotes-labels (NORP, EVENT, WORK_OF_ART, …) zijn voor v0.3 te vaag.
_SPACY_LABEL_TO_TYPE: Final[dict[str, EntityType]] = {
    "PER": EntityType.NAME,
    "PERSON": EntityType.NAME,
    "ORG": EntityType.ORG,
    "LOC": EntityType.LOCATION,
    "GPE": EntityType.LOCATION,
    "FAC": EntityType.LOCATION,
}

# Vaste pseudo-confidence per label. spaCy NER geeft geen native per-entity
# probability terug; om de threshold-routing toch werkend te krijgen kennen
# we per label een constante toe die net boven de default-threshold ligt.
# Operationele consequentie: de review-queue voor spaCy is een handmatige
# knop (operator zet threshold > constante), geen probabilistische trigger.
# Voor echte per-entity probabilities zie v1.0 met spacy-transformers.
_SPACY_LABEL_CONFIDENCE: Final[dict[str, float]] = {
    "PER": 0.90,
    "PERSON": 0.90,
    "ORG": 0.85,
    "LOC": 0.90,
    "GPE": 0.85,
    "FAC": 0.80,
}


@lru_cache(maxsize=1)
def _get_spacy_nlp() -> Any:
    """Lazy laad het NL-model; gecached voor de proces-lifetime.

    Bewust lazy: tests die alleen regex valideren betalen de ~5s model-load
    niet, en bij `ollama serve`-flows hoeft het spaCy-model niet in geheugen
    te zitten als de gebruiker laag 2 uitschakelt.
    """
    import spacy  # noqa: PLC0415

    return spacy.load(settings.spacy_model)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Thresholds:
    """Confidence-grenzen per laag (en per spaCy-label).

    Defaults matchen het prompt; `from_db()` overschrijft met waardes uit
    de `config`-tabel als die gezet zijn. Tests construeren bewust een
    expliciete `Thresholds()` zodat ze niet van een DB-init afhangen.
    """

    regex: float = 1.0
    spacy_person: float = 0.85
    spacy_org: float = 0.80
    spacy_location: float = 0.85
    llm: float = 0.70

    @classmethod
    def from_db(cls) -> Thresholds:
        return cls(
            regex=_read_float("threshold_regex", cls.regex),
            spacy_person=_read_float("threshold_spacy_person", cls.spacy_person),
            spacy_org=_read_float("threshold_spacy_org", cls.spacy_org),
            spacy_location=_read_float("threshold_spacy_location", cls.spacy_location),
            llm=_read_float("threshold_llm", cls.llm),
        )

    def for_entity(self, entity: Entity) -> float:
        # Layer + entity_type bepalen welke knob telt. Voor REGEX en LLM is
        # er één globale waarde per laag; voor SPACY hangt het af van het
        # label dat de NER opleverde.
        if entity.detection_layer is DetectionLayer.REGEX:
            return self.regex
        if entity.detection_layer is DetectionLayer.LLM:
            return self.llm
        return {
            EntityType.NAME: self.spacy_person,
            EntityType.ORG: self.spacy_org,
            EntityType.LOCATION: self.spacy_location,
        }.get(entity.entity_type, 1.0)


def _read_float(key: str, default: float) -> float:
    # Defensieve cast: als iemand handmatig een non-numeriek waarde in de
    # config-tabel zet, falt fail-fast in `detect_all()` niet in de pijplijn.
    raw = get_config_value(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Config %r had niet-numerieke waarde %r; val terug op default %s",
            key,
            raw,
            default,
        )
        return default


# ---------------------------------------------------------------------------
# Layer-implementaties
# ---------------------------------------------------------------------------


def detect_regex(text: str) -> list[Entity]:
    """Laag 1: deterministische patroon-detectie met validators.

    Volgorde: eerst context-date-patronen (claimen hun datum-span), dan
    de algemene patternlist op volgorde. Geclaimde spans worden door latere
    matches overgeslagen.
    """
    entities: list[Entity] = []
    claimed: list[tuple[int, int]] = []

    for entity_type, pattern in CONTEXT_DATE_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(1), match.end(1))
            if _overlaps_any(span, claimed):
                continue
            entities.append(
                Entity(
                    original=match.group(1),
                    entity_type=entity_type,
                    confidence=1.0,
                    detection_layer=DetectionLayer.REGEX,
                    start=span[0],
                    end=span[1],
                )
            )
            claimed.append(span)

    for entity_type, pattern, validator in REGEX_PATTERNS:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if _overlaps_any(span, claimed):
                continue
            matched_text = match.group(0)
            if validator is not None and not validator(matched_text):
                # BSN-elfproef of IBAN-mod-97 zegt nee: niet als entity
                # registreren en de span vrijhouden zodat een later patroon
                # er nog over kan vallen.
                continue
            entities.append(
                Entity(
                    original=matched_text,
                    entity_type=entity_type,
                    confidence=1.0,
                    detection_layer=DetectionLayer.REGEX,
                    start=span[0],
                    end=span[1],
                )
            )
            claimed.append(span)

    return entities


def detect_spacy_with_status(text: str) -> tuple[list[Entity], LayerStatus]:
    """Laag 2 met expliciete beschikbaarheidsstatus voor de voortgangsindicator.

    Soft-fail bij ontbrekend model — laag 1 heeft al gedraaid, dus de
    pijplijn werkt door zonder spaCy. De status onderscheidt "gedraaid"
    (`OK`) van "model niet geïnstalleerd" (`UNAVAILABLE`) zodat de UI het
    verschil kan tonen.
    """
    try:
        nlp = _get_spacy_nlp()
    except OSError as exc:
        logger.warning("Laag 2 (spaCy %r) niet beschikbaar: %s", settings.spacy_model, exc)
        return [], LayerStatus.UNAVAILABLE

    doc = nlp(text)
    entities: list[Entity] = []
    for ent in doc.ents:
        entity_type = _SPACY_LABEL_TO_TYPE.get(ent.label_)
        if entity_type is None:
            continue
        start, end = ent.start_char, ent.end_char
        if entity_type is EntityType.NAME:
            start, end = expand_name_span(text, start, end)
        entities.append(
            Entity(
                original=text[start:end],
                entity_type=entity_type,
                confidence=_SPACY_LABEL_CONFIDENCE.get(ent.label_, 0.85),
                detection_layer=DetectionLayer.SPACY,
                start=start,
                end=end,
            )
        )
    return entities, LayerStatus.OK


def detect_spacy(text: str) -> list[Entity]:
    """Laag 2: spaCy NER voor NAME/ORG/LOCATION (zie `detect_spacy_with_status`)."""
    return detect_spacy_with_status(text)[0]


_LLM_SYSTEM_PROMPT: Final[str] = (
    "Je bent een NER-assistent voor Nederlandse zorgteksten. Identificeer "
    "alléén product-namen (medicijnen, apparaten) en project-codes. Negeer "
    "alle andere entity-soorten — die zijn al elders gedetecteerd. Antwoord "
    'uitsluitend in JSON: {"entities": [{"text": "...", '
    '"type": "product"|"project", "confidence": 0.0-1.0}]}'
)


class Layer3BackendError(RuntimeError):
    """Laag-3-backend vereist maar niet beschikbaar (eval fail-hard)."""


class Layer3Backend(Protocol):
    """Transport voor laag 3: lever ruwe JSON-tekst voor (system, user).

    Door de transport te abstraheren kan de runtime Ollama gebruiken terwijl
    het eval-harnas alternatieve lokale backends (bv. MLX) injecteert voor de
    modelvergelijking (TESTPLAN.md §8), zonder de rest van de pijplijn te
    dupliceren. Implementaties mogen excepties gooien bij falen; de caller
    vangt die soft op.
    """

    name: str
    model: str

    def complete(self, system: str, user: str) -> str: ...


class OllamaBackend:
    """Default laag-3-backend: lokaal Ollama-model met JSON-grammar."""

    name = "ollama"

    def __init__(self, host: str | None = None, model: str | None = None) -> None:
        self.host = host or settings.ollama_host
        self.model = model or settings.ollama_model

    def complete(self, system: str, user: str) -> str:
        import ollama  # noqa: PLC0415

        client = ollama.Client(host=self.host)
        response = client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            format="json",
            options={"temperature": 0.0},
        )
        return str(response["message"]["content"])


def detect_llm_with_status(
    text: str, *, backend: Layer3Backend | None = None
) -> tuple[list[Entity], LayerStatus]:
    """Laag 3 met expliciete beschikbaarheidsstatus voor de voortgangsindicator.

    Soft-fail op elke fout: timeout, JSON-parse, model ontbreekt, backend
    niet bereikbaar — laag 1+2 hebben hun werk al gedaan en de proxy mag
    niet stoppen wegens een optionele laag. De status is `UNAVAILABLE`
    zodra de aanroep faalt, zodat de UI "niet beschikbaar" kan tonen.

    `backend` default naar `OllamaBackend()`; het eval-harnas kan een
    alternatieve backend (bv. MLX) injecteren voor de modelvergelijking.
    """
    active = backend or OllamaBackend()
    try:
        content = active.complete(_LLM_SYSTEM_PROMPT, text)
    except Exception as exc:  # noqa: BLE001 (bewust breed: laag 3 faalt soft per BR)
        if isinstance(exc, Layer3BackendError):
            raise
        if getattr(active, "fail_hard", False):
            hint_fn = getattr(active, "unavailable_hint", None)
            hint = f"\n{hint_fn()}" if hint_fn is not None else ""
            raise Layer3BackendError(
                f"Laag 3 ({active.name}) niet beschikbaar: {type(exc).__name__}: {exc}{hint}"
            ) from exc
        logger.warning(
            "Laag 3 (%s) faalde: %s: %s — sla LLM-detectie over",
            active.name,
            type(exc).__name__,
            exc,
        )
        return [], LayerStatus.UNAVAILABLE

    import json  # noqa: PLC0415  # lazy: alleen nodig als LLM-pad actief is

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        if getattr(active, "fail_hard", False):
            raise Layer3BackendError(
                f"Laag 3 ({active.name}) gaf geen geldige JSON: {exc}"
            ) from exc
        logger.warning("Laag 3 (%s) gaf geen geldige JSON: %s", active.name, exc)
        return [], LayerStatus.UNAVAILABLE

    return _llm_entities_from_payload(text, parsed), LayerStatus.OK


def detect_llm(text: str, *, backend: Layer3Backend | None = None) -> list[Entity]:
    """Laag 3: lokaal LLM voor jargon/productnamen (zie `*_with_status`)."""
    return detect_llm_with_status(text, backend=backend)[0]


_LLM_TYPE_MAP: Final[dict[str, EntityType]] = {
    "product": EntityType.PRODUCT,
    "project": EntityType.PROJECT,
}


def detector_layers_by_type() -> dict[EntityType, tuple[DetectionLayer, ...]]:
    """Welke detectielaag/lagen elk `EntityType` in principe kan detecteren.

    Afgeleid van de daadwerkelijke detector-structuren (regex-patronen, spaCy-
    en LLM-mappings) zodat deze map automatisch in sync blijft met de
    implementatie. Types zonder enige detector — `ADDRESS`, `DIAGNOSIS` en
    generalisatie-output (`BIRTH_YEAR`, `POSTCODE_PC2`) — ontbreken bewust in
    het resultaat (geen detector). De laag-volgorde is regex → spaCy → LLM.
    """
    layers: dict[EntityType, set[DetectionLayer]] = {}
    for etype, _pattern in CONTEXT_DATE_PATTERNS:
        layers.setdefault(etype, set()).add(DetectionLayer.REGEX)
    for etype, _pattern, _validator in REGEX_PATTERNS:
        layers.setdefault(etype, set()).add(DetectionLayer.REGEX)
    for etype in _SPACY_LABEL_TO_TYPE.values():
        layers.setdefault(etype, set()).add(DetectionLayer.SPACY)
    for etype in _LLM_TYPE_MAP.values():
        layers.setdefault(etype, set()).add(DetectionLayer.LLM)

    order = (DetectionLayer.REGEX, DetectionLayer.SPACY, DetectionLayer.LLM)
    return {
        etype: tuple(layer for layer in order if layer in found)
        for etype, found in layers.items()
    }


def _llm_entities_from_payload(text: str, payload: object) -> list[Entity]:
    if not isinstance(payload, dict):
        return []
    raw_items = payload.get("entities", [])
    if not isinstance(raw_items, list):
        return []

    entities: list[Entity] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        entity_text = raw.get("text")
        entity_type_str = raw.get("type")
        if not isinstance(entity_text, str) or not isinstance(entity_type_str, str):
            continue
        entity_type = _LLM_TYPE_MAP.get(entity_type_str.lower())
        if entity_type is None:
            continue
        # LLMs zijn slecht in offsets; pak de eerste case-insensitive match
        # in de originele tekst. Lukt dat niet, skip — geen verzonnen span.
        idx = text.lower().find(entity_text.lower())
        if idx < 0:
            continue
        confidence_raw = raw.get("confidence", 0.7)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))
        entities.append(
            Entity(
                original=text[idx : idx + len(entity_text)],
                entity_type=entity_type,
                confidence=confidence,
                detection_layer=DetectionLayer.LLM,
                start=idx,
                end=idx + len(entity_text),
            )
        )
    return entities


# ---------------------------------------------------------------------------
# Orchestratie
# ---------------------------------------------------------------------------


def detect_all(
    text: str,
    *,
    use_llm: bool = False,
    thresholds: Thresholds | None = None,
    llm_backend: Layer3Backend | None = None,
) -> DetectionResult:
    """Combineer alle lagen en route op confidence-threshold.

    Dunne wrapper rond `detect_all_timed` die de per-laag-timing weggooit;
    bestaande callers (proxy-pijplijn, eval) houden dezelfde signatuur.
    `llm_backend` injecteert optioneel een alternatieve laag-3-backend.
    """
    result, _timings = detect_all_timed(
        text, use_llm=use_llm, thresholds=thresholds, llm_backend=llm_backend
    )
    return result


def detect_all_timed(
    text: str,
    *,
    use_llm: bool = False,
    thresholds: Thresholds | None = None,
    on_layer: Callable[[list[LayerTiming]], None] | None = None,
    llm_backend: Layer3Backend | None = None,
) -> tuple[DetectionResult, list[LayerTiming]]:
    """Als `detect_all`, maar meet elke laag en levert per-laag-timing.

    - `use_llm=False` (default): alleen regex + spaCy; laag 3 krijgt status
      `DISABLED`.
    - `on_layer`: optionele callback die na elke laag-overgang wordt
      aangeroepen met de timings-tot-nu-toe (incl. een `RUNNING`-entry voor
      de laag die nu draait). Hiermee kan de UI live updaten.

    Latere lagen die met eerdere overlappen worden gedropt — dit is de
    "earlier-layer-wins"-regel die ervoor zorgt dat een spaCy-NAME niet
    een al gedetecteerde BSN kan opslokken.
    """
    effective_thresholds = thresholds if thresholds is not None else Thresholds.from_db()
    timings: list[LayerTiming] = []

    def _emit(running: LayerTiming | None = None) -> None:
        if on_layer is not None:
            on_layer([*timings, running] if running is not None else list(timings))

    # Laag 1 — regex (deterministisch, altijd beschikbaar).
    _emit(LayerTiming(DetectionLayer.REGEX, LayerStatus.RUNNING, None, 0))
    start = perf_counter()
    regex_entities = detect_regex(text)
    timings.append(
        LayerTiming(
            DetectionLayer.REGEX,
            LayerStatus.OK,
            (perf_counter() - start) * 1000.0,
            len(regex_entities),
        )
    )
    _emit()

    # Laag 2 — spaCy NER.
    _emit(LayerTiming(DetectionLayer.SPACY, LayerStatus.RUNNING, None, 0))
    start = perf_counter()
    spacy_entities, spacy_status = detect_spacy_with_status(text)
    timings.append(
        LayerTiming(
            DetectionLayer.SPACY,
            spacy_status,
            (perf_counter() - start) * 1000.0,
            len(spacy_entities),
        )
    )
    _emit()

    # Laag 3 — lokaal LLM (optioneel).
    if use_llm:
        _emit(LayerTiming(DetectionLayer.LLM, LayerStatus.RUNNING, None, 0))
        start = perf_counter()
        llm_entities, llm_status = detect_llm_with_status(text, backend=llm_backend)
        timings.append(
            LayerTiming(
                DetectionLayer.LLM,
                llm_status,
                (perf_counter() - start) * 1000.0,
                len(llm_entities),
            )
        )
    else:
        llm_entities = []
        timings.append(LayerTiming(DetectionLayer.LLM, LayerStatus.DISABLED, None, 0))
    _emit()

    merged = _merge_cross_layer((regex_entities, spacy_entities, llm_entities))
    return _route_by_threshold(merged, effective_thresholds), timings


def _merge_cross_layer(
    layer_lists: Iterable[list[Entity]],
) -> list[Entity]:
    """Behoud entiteiten in volgorde van layer-prioriteit; drop overlappers.

    Eerste laag (REGEX) wordt integraal opgenomen; spaCy en LLM kunnen
    toevoegen waar regex niets vond, maar nooit een regex-detectie
    overschrijven (PLAN.md §8: "later layers add, not overwrite").
    """
    merged: list[Entity] = []
    for layer in layer_lists:
        for entity in layer:
            span = (entity.start, entity.end)
            if any(_overlap(span, (existing.start, existing.end)) for existing in merged):
                continue
            merged.append(entity)
    return merged


def _route_by_threshold(entities: list[Entity], thresholds: Thresholds) -> DetectionResult:
    """Splits entities op `>= threshold` (BR-A04)."""
    confident: list[Entity] = []
    pending: list[Entity] = []
    for entity in entities:
        if entity.confidence >= thresholds.for_entity(entity):
            confident.append(entity)
        else:
            pending.append(entity)
    return DetectionResult(confident_entities=confident, pending_review=pending)


# ---------------------------------------------------------------------------
# Span-helpers
# ---------------------------------------------------------------------------


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    # Half-open intervallen: [start, end). Twee spans overlappen als geen
    # van beide volledig vóór de ander ligt.
    return not (a[1] <= b[0] or b[1] <= a[0])


def _overlaps_any(span: tuple[int, int], claimed: list[tuple[int, int]]) -> bool:
    return any(_overlap(span, c) for c in claimed)
