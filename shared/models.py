"""Domeinvocabulaire: Enums, statische mappings, Pydantic-modellen.

Eén canonical bron voor alle entity-typering die zowel in DB-serialisatie,
HTTP-validatie als UI-rendering opduikt. Bevat bewust geen I/O en geen
imports uit andere `shared`-modules — dit is de leaf van de package.
"""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EntityType(StrEnum):
    """Concrete entity-soorten die Pylades herkent.

    `StrEnum` (Python 3.11+) zorgt dat Pydantic en sqlite3 zonder custom
    serializer de Enum-waarde als TEXT opslaan; dat scheelt een conversie-
    laag op elke DB-write en JSON-emit.
    """

    BSN = "bsn"
    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    IBAN = "iban"
    POSTCODE_PC6 = "postcode_pc6"
    POSTCODE_PC2 = "postcode_pc2"
    ADDRESS = "address"
    BIRTHDATE = "birthdate"
    BIRTH_YEAR = "birth_year"
    AGE = "age"
    MRN = "mrn"
    EPD_ID = "epd_id"
    KENTEKEN = "kenteken"
    ICD10_CODE = "icd10_code"
    DIAGNOSIS = "diagnosis"
    ADMISSION_DATE = "admission_date"
    DISCHARGE_DATE = "discharge_date"
    EXAM_DATE = "exam_date"
    ORG = "org"
    LOCATION = "location"
    PRODUCT = "product"
    PROJECT = "project"


class EntityCategory(StrEnum):
    """Privacy-categorieën conform BR-A01."""

    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    FREE_TEXT = "free_text"
    CLINICAL_SENSITIVE = "clinical_sensitive"


class DetectionLayer(StrEnum):
    """Welke detectielaag heeft de entity gevonden."""

    REGEX = "regex"
    SPACY = "spacy"
    LLM = "llm"


class ReviewStatus(StrEnum):
    """Status van een item in de manual-review-queue."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"


class PseudonymizationMode(StrEnum):
    """Effectieve pseudonimiseringsmodus per entity (BR-C06)."""

    ONE_WAY = "one_way"
    TWO_WAY = "two_way"


# Mapping van entity-type naar privacy-categorie (BR-A01).
# Bewust een externe dict en geen method op de Enum: dit is *beleid*, geen
# intrinsieke eigenschap, en moet als tabel reviewbaar zijn.
ENTITY_CATEGORY_MAP: dict[EntityType, EntityCategory] = {
    EntityType.BSN: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.NAME: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.EMAIL: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.PHONE: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.IBAN: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.POSTCODE_PC6: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.ADDRESS: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.MRN: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.EPD_ID: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.KENTEKEN: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.BIRTHDATE: EntityCategory.DIRECT_IDENTIFIER,
    EntityType.POSTCODE_PC2: EntityCategory.QUASI_IDENTIFIER,
    EntityType.BIRTH_YEAR: EntityCategory.QUASI_IDENTIFIER,
    EntityType.AGE: EntityCategory.QUASI_IDENTIFIER,
    EntityType.ORG: EntityCategory.QUASI_IDENTIFIER,
    EntityType.LOCATION: EntityCategory.QUASI_IDENTIFIER,
    EntityType.ADMISSION_DATE: EntityCategory.QUASI_IDENTIFIER,
    EntityType.DISCHARGE_DATE: EntityCategory.QUASI_IDENTIFIER,
    EntityType.EXAM_DATE: EntityCategory.QUASI_IDENTIFIER,
    EntityType.ICD10_CODE: EntityCategory.CLINICAL_SENSITIVE,
    EntityType.DIAGNOSIS: EntityCategory.CLINICAL_SENSITIVE,
    EntityType.PRODUCT: EntityCategory.FREE_TEXT,
    EntityType.PROJECT: EntityCategory.FREE_TEXT,
}


# Drie-letter codes die in pseudoniem-format `[XXX-aaaaaa]` verschijnen (BR-C01).
# Hier (in models, niet in crypto) omdat het metadata over EntityType is:
# wie een nieuw type toevoegt, moet in één file ENTITY_CATEGORY_MAP en
# SHORT_TYPE_CODES uitbreiden — de import-time check hieronder dwingt dat af.
SHORT_TYPE_CODES: dict[EntityType, str] = {
    EntityType.BSN: "BSN",
    EntityType.NAME: "PER",
    EntityType.EMAIL: "EML",
    EntityType.PHONE: "TEL",
    EntityType.IBAN: "IBN",
    EntityType.POSTCODE_PC6: "PC6",
    EntityType.POSTCODE_PC2: "PC2",
    EntityType.ADDRESS: "ADR",
    EntityType.BIRTHDATE: "BDT",
    EntityType.BIRTH_YEAR: "BYR",
    EntityType.AGE: "AGE",
    EntityType.MRN: "MRN",
    EntityType.EPD_ID: "EPD",
    EntityType.KENTEKEN: "KEN",
    EntityType.ICD10_CODE: "ICD",
    EntityType.DIAGNOSIS: "DGN",
    EntityType.ADMISSION_DATE: "ADM",
    EntityType.DISCHARGE_DATE: "DCH",
    EntityType.EXAM_DATE: "EXM",
    EntityType.ORG: "ORG",
    EntityType.LOCATION: "LOC",
    EntityType.PRODUCT: "PRD",
    EntityType.PROJECT: "PRJ",
}


def _check_metadata_completeness() -> None:
    # Import-time fail-fast: als een nieuwe EntityType vergeten is in een
    # van de mappings, weet je dat bij `import shared.models`, niet pas
    # bij de eerste detect-call midden in een sessie.
    missing_category = set(EntityType) - set(ENTITY_CATEGORY_MAP.keys())
    missing_code = set(EntityType) - set(SHORT_TYPE_CODES.keys())
    if missing_category:
        raise RuntimeError(f"ENTITY_CATEGORY_MAP mist EntityTypes: {sorted(missing_category)}")
    if missing_code:
        raise RuntimeError(f"SHORT_TYPE_CODES mist EntityTypes: {sorted(missing_code)}")


_check_metadata_completeness()


_PROMPT_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class Template(BaseModel):
    """Opdracht-template met per-entity pseudonimiseringsmodus (BR-C06).

    `mode_overrides` is een dict in plaats van een aparte tabel: overrides
    worden altijd samen met de template gelezen, zijn klein (~20 keys), en
    nooit cross-template geaggregeerd in v0.3.

    `prompt_tekst` bevat per design *uitsluitend* de LLM-opdracht plus
    exact één `{input}`-placeholder voor de patiëntdossier-tekst (zie PLAN
    §15a). Andere `{naam}`-placeholders worden afgekeurd zodat ze niet
    stilzwijgend naar het LLM lekken. Een lege string blijft toegestaan
    voor edge-cases (technische default tijdens migratie).
    """

    model_config = ConfigDict(use_enum_values=False)

    id: int | None = None
    groep: str = Field(min_length=1)
    naam: str = Field(min_length=1)
    beschrijving: str = ""
    llm_provider: str = Field(min_length=1)
    llm_naam: str = Field(min_length=1)
    prompt_tekst: str = ""
    max_tokens: int = Field(default=16_000, gt=0)
    use_llm: bool = Field(
        default=False,
        description=(
            "Schakelt detectielaag 3 (lokaal Ollama-LLM voor product- en "
            "projectnamen) in voor deze template. Default uit; vereist een "
            "draaiende Ollama-server met het geconfigureerde model."
        ),
    )
    default_mode: PseudonymizationMode | None = None
    mode_overrides: dict[EntityType, PseudonymizationMode] = Field(default_factory=dict)
    two_way_justification: str | None = None
    sort_order: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _two_way_requires_justification(self) -> "Template":
        # BR-C06: zodra ergens TWO_WAY actief is, eisen we een onderbouwing.
        has_two_way = self.default_mode == PseudonymizationMode.TWO_WAY or any(
            mode == PseudonymizationMode.TWO_WAY for mode in self.mode_overrides.values()
        )
        if has_two_way and not (self.two_way_justification and self.two_way_justification.strip()):
            raise ValueError(
                "two_way_justification is verplicht zodra een TWO_WAY-modus actief is (BR-C06)"
            )
        return self

    @model_validator(mode="after")
    def _prompt_tekst_has_single_input_placeholder(self) -> "Template":
        text = self.prompt_tekst
        if not text:
            return self
        placeholders = _PROMPT_PLACEHOLDER_RE.findall(text)
        others = sorted({p for p in placeholders if p != "input"})
        input_count = sum(1 for p in placeholders if p == "input")
        if input_count != 1 or others:
            raise ValueError(
                "prompt_tekst moet exact één `{input}`-placeholder bevatten en "
                "geen andere `{naam}`-placeholders "
                f"(gevonden: {input_count}× `{{input}}`, andere placeholders: {others})"
            )
        return self


class Entity(BaseModel):
    """Eén detectie in een prompt.

    Lifecycle: detect (set: original, entity_type, confidence, layer, start, end)
    -> review (kan entity_type wijzigen of droppen) -> generalize (kan type
    veranderen en `generalized_to` zetten) -> resolve_mode (zet
    `effective_mode`) -> pseudonymize (zet `pseudonym`). Vandaar dat de
    laatste drie velden optioneel zijn.
    """

    model_config = ConfigDict(use_enum_values=False)

    original: str = Field(min_length=1)
    entity_type: EntityType
    category: EntityCategory | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    detection_layer: DetectionLayer
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    pseudonym: str | None = None
    generalized_to: str | None = None
    effective_mode: PseudonymizationMode | None = None
    rare_icd_review: bool = Field(
        default=False,
        description="BR-B05: ICD-10 komt voor in RARE_ICD10_CODES → manual review",
    )

    @model_validator(mode="after")
    def _derive_and_validate(self) -> "Entity":
        # `category` wordt afgeleid uit `entity_type` zodat de twee nooit
        # kunnen divergeren; expliciet meegeven is wel toegestaan voor het
        # geval een caller bewust een gewijzigde categorie wil registreren.
        if self.category is None:
            self.category = ENTITY_CATEGORY_MAP[self.entity_type]
        if self.end < self.start:
            raise ValueError(f"Entity.end ({self.end}) ligt vóór start ({self.start})")
        return self


class ReviewItem(BaseModel):
    """Eén regel in de manual-review-queue (BR-A04)."""

    id: int | None = None
    session_id: str = Field(min_length=1)
    original_text: str
    detected_text: str
    proposed_entity_type: EntityType
    proposed_category: EntityCategory
    confidence: float = Field(ge=0.0, le=1.0)
    detection_layer: DetectionLayer
    status: ReviewStatus = ReviewStatus.PENDING
    user_decision_entity_type: EntityType | None = None
    user_decision_at: datetime | None = None
    user_decision_note: str | None = None
    created_at: datetime | None = None


class DetectionResult(BaseModel):
    """Uitkomst van `detect_all()`.

    Twee aparte lists in plaats van één list met status-veld: de consumer
    (`proxy/main.py`) heeft fundamenteel verschillende vervolgacties voor
    de twee buckets — eenheid in datatype zou filter-aware code afdwingen
    en bug-kansen vergroten.
    """

    confident_entities: list[Entity] = Field(default_factory=list)
    pending_review: list[Entity] = Field(default_factory=list)


class PseudonymizationResult(BaseModel):
    """Resultaat van detect+generalize+pseudonymize voor één prompt."""

    session_id: str = Field(min_length=1)
    original: str
    pseudonymized: str
    entities: list[Entity] = Field(default_factory=list)
    avg_confidence: float = Field(ge=0.0, le=1.0)
    review_required: bool = False


class AuditEntry(BaseModel):
    """Eén regel in `audit_log` (BR-G01).

    Plain Pydantic-model (geen ORM): de tabel is append-only, kolommen zijn
    stabiel, en we willen dat de UI een entry rechtstreeks kan deserialiseren
    zonder lazy-loading of joins. `id` en `created_at` zijn `None` tot na de
    eerste DB-roundtrip; de andere optionele velden zijn echt nullable in de
    use-case (bv. `error` is alleen gezet bij een mislukte upstream-call).
    """

    id: int | None = None
    session_id: str = Field(min_length=1)
    template_id: int | None = None
    original_prompt: str
    pseudonymized_prompt: str
    response_pseudonymized: str | None = None
    response_depseudonymized: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    avg_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    review_required: bool = False
    error: str | None = None
    created_at: datetime | None = None
