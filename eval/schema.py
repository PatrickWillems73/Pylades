"""Dataset-schema + JSONL-I/O voor het eval-harnas.

Eén record = één synthetisch dossier met **span-level** ground truth op het
*detectie*-niveau (origineel + type + offsets, vóór generalisatie). De
generalisatie-verwachting staat los in `expected_generalization`, zodat we
detectie-kwaliteit en generalisatie-transformatie apart kunnen meten.

Offsets zijn half-open `[start, end)` op de Python-string `prompt`. De
model-validator dwingt `prompt[start:end] == text` af zodat een verkeerd
gelabelde span niet stilzwijgend de metrics vervuilt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.models import ENTITY_CATEGORY_MAP, EntityCategory, EntityType


class GoldEntity(BaseModel):
    """Eén gelabelde entity in een dossier (ground truth)."""

    model_config = ConfigDict(use_enum_values=False)

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str = Field(min_length=1)
    type: EntityType
    category: EntityCategory | None = None
    note: str = ""

    @model_validator(mode="after")
    def _derive_category_and_check_span(self) -> GoldEntity:
        if self.category is None:
            self.category = ENTITY_CATEGORY_MAP[self.type]
        if self.end <= self.start:
            raise ValueError(f"GoldEntity.end ({self.end}) moet > start ({self.start}) zijn")
        return self


class EvalRecord(BaseModel):
    """Eén dossier met ground truth."""

    model_config = ConfigDict(use_enum_values=False)

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    entities: list[GoldEntity] = Field(default_factory=list)
    seed: int = 0
    scenario: str = ""
    difficulty: str = "normal"
    expected_generalization: dict[str, str] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_offsets_align(self) -> EvalRecord:
        for ent in self.entities:
            actual = self.prompt[ent.start : ent.end]
            if actual != ent.text:
                raise ValueError(
                    f"Record {self.id!r}: offset-mismatch voor {ent.type.value!r}: "
                    f"prompt[{ent.start}:{ent.end}]={actual!r} != text={ent.text!r}"
                )
        return self

    def direct_identifiers(self) -> list[GoldEntity]:
        return [e for e in self.entities if e.category is EntityCategory.DIRECT_IDENTIFIER]


def load_jsonl(path: str | Path) -> list[EvalRecord]:
    """Lees een dataset uit JSONL; valideert elk record."""
    records: list[EvalRecord] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                records.append(EvalRecord.model_validate_json(line))
            except Exception as exc:  # noqa: BLE001 — context toevoegen en doorgooien
                raise ValueError(f"Regel {line_no} in {path} is ongeldig: {exc}") from exc
    return records


def dump_jsonl(records: list[EvalRecord], path: str | Path) -> None:
    """Schrijf records als JSONL (één compacte JSON per regel)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(record.model_dump_json())
            fh.write("\n")
