"""Semantische validators voor een gelabelde dataset.

De offset-alignment (`prompt[start:end] == text`) wordt al door het Pydantic-
model afgedwongen. Hier checken we domein-consistentie die fouten in de
ground truth aan het licht brengt vóór ze de metrics vertekenen:

- BSN-labels moeten de elfproef passeren (anders is het geen BSN).
- IBAN-labels moeten de mod-97-check passeren.
- record-id's zijn uniek.
- gelabelde categorie matcht de canonieke `ENTITY_CATEGORY_MAP`.
- zeldzame ICD-codes in de tekst die als ICD10_CODE gelabeld zijn worden
  geteld (informatief, geen harde fout).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from data.icd10_rare import RARE_ICD10_CODES
from eval.schema import EvalRecord
from shared.crypto import validate_bsn_elfproef, validate_iban_checksum
from shared.models import ENTITY_CATEGORY_MAP, EntityType


@dataclass
class ValidationReport:
    """Uitkomst van een dataset-validatie."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    record_count: int = 0
    entity_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_dataset(records: list[EvalRecord]) -> ValidationReport:
    report = ValidationReport(record_count=len(records))

    seen_ids: Counter[str] = Counter(r.id for r in records)
    for rid, count in seen_ids.items():
        if count > 1:
            report.errors.append(f"Dubbel record-id {rid!r} komt {count}x voor")

    for record in records:
        for ent in record.entities:
            report.entity_count += 1
            canonical = ENTITY_CATEGORY_MAP[ent.type]
            if ent.category is not None and ent.category is not canonical:
                report.errors.append(
                    f"{record.id}: categorie {ent.category.value!r} voor type "
                    f"{ent.type.value!r} wijkt af van canoniek {canonical.value!r}"
                )

            if ent.type is EntityType.BSN and not validate_bsn_elfproef(ent.text):
                report.errors.append(
                    f"{record.id}: BSN-label {ent.text!r} faalt de elfproef"
                )
            if ent.type is EntityType.IBAN and not validate_iban_checksum(ent.text):
                report.errors.append(
                    f"{record.id}: IBAN-label {ent.text!r} faalt de mod-97-check"
                )
            if ent.type is EntityType.ICD10_CODE and ent.text.strip().upper() in RARE_ICD10_CODES:
                report.warnings.append(
                    f"{record.id}: zeldzame ICD-code {ent.text!r} (BR-B05 review-kandidaat)"
                )

    return report
