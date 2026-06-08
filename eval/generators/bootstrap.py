"""Deterministische, offline gelabelde bootstrap-dataset.

Geen API nodig: de prompts worden opgebouwd uit geordende segmenten, waarbij
de offsets programmatisch berekend worden (geen handmatig tellen → geen
offset-fouten). Dekt elke detectielaag, de generalisatie-regels (BR-B01..B05)
en bewuste edge-cases, inclusief de bekende detector-gaten (`ADDRESS`,
`DIAGNOSIS`) die als structurele false-negatives zichtbaar moeten worden.

Draai: `python -m eval.generators.bootstrap`
"""

from __future__ import annotations

from pathlib import Path

from data.fixtures import INVALID_BSN, VALID_BSN, VALID_NL_IBAN
from eval.manifest import write_manifest
from eval.schema import EvalRecord, GoldEntity, dump_jsonl
from eval.validators import validate_dataset
from shared.models import EntityType

# Een segment is platte tekst (str) of een (tekst, type)-paar dat als
# GoldEntity gelabeld wordt.
Segment = str | tuple[str, EntityType]

DATASET_DIR = Path(__file__).resolve().parents[1] / "datasets" / "bootstrap"
DATASET_FILE = DATASET_DIR / "dataset.jsonl"
DATASET_VERSION = "bootstrap-v1"


def _build(
    rid: str,
    scenario: str,
    difficulty: str,
    segments: list[Segment],
    expected_generalization: dict[str, str] | None = None,
) -> EvalRecord:
    parts: list[str] = []
    entities: list[GoldEntity] = []
    pos = 0
    for seg in segments:
        if isinstance(seg, tuple):
            text, etype = seg
            entities.append(GoldEntity(start=pos, end=pos + len(text), text=text, type=etype))
            parts.append(text)
            pos += len(text)
        else:
            parts.append(seg)
            pos += len(seg)
    return EvalRecord(
        id=rid,
        prompt="".join(parts),
        entities=entities,
        scenario=scenario,
        difficulty=difficulty,
        expected_generalization=expected_generalization or {},
        meta={"source": "bootstrap"},
    )


def build_records() -> list[EvalRecord]:
    return [
        _build(
            "basic_patient",
            "patient_intro",
            "normal",
            [
                "Mevrouw ",
                ("Pietersen", EntityType.NAME),
                ", BSN ",
                (VALID_BSN, EntityType.BSN),
                ", woont op postcode ",
                ("7411AB", EntityType.POSTCODE_PC6),
                " in ",
                ("Deventer", EntityType.LOCATION),
                ". Geboren op ",
                ("03-04-1972", EntityType.BIRTHDATE),
                ".",
            ],
            {"7411AB": "74", "03-04-1972": "1972"},
        ),
        _build(
            "clinical_mrn_epd",
            "clinical_note",
            "normal",
            [
                "Patiënt met ",
                ("MRN1234567", EntityType.MRN),
                " en ",
                ("EPD-789012", EntityType.EPD_ID),
                " werd opgenomen op ",
                ("15-03-2024", EntityType.ADMISSION_DATE),
                " met diagnose ",
                ("J45.0", EntityType.ICD10_CODE),
                " (astma bronchiale).",
            ],
            {"15-03-2024": "2024-03"},
        ),
        _build(
            "rare_icd",
            "clinical_note",
            "normal",
            [
                "Genetisch onderzoek bevestigt diagnose ",
                ("G71.0", EntityType.ICD10_CODE),
                " bij betrokkene.",
            ],
        ),
        _build(
            "contact_details",
            "contact_details",
            "normal",
            [
                "Stuur de factuur naar ",
                ("pietersen@voorbeeld.nl", EntityType.EMAIL),
                ", IBAN ",
                (VALID_NL_IBAN, EntityType.IBAN),
                ", telefoon ",
                ("06-12345678", EntityType.PHONE),
                ". De bedrijfsauto heeft kenteken ",
                ("12-AB-345", EntityType.KENTEKEN),
                ".",
            ],
        ),
        _build(
            "org_location_pc6",
            "referral",
            "normal",
            [
                ("OLVG", EntityType.ORG),
                " ",
                ("Amsterdam", EntityType.LOCATION),
                " (postcode ",
                ("1091AC", EntityType.POSTCODE_PC6),
                ") overlegt met het ",
                ("Catharina Ziekenhuis", EntityType.ORG),
                " in ",
                ("Eindhoven", EntityType.LOCATION),
                " (",
                ("5623EJ", EntityType.POSTCODE_PC6),
                ").",
            ],
            {"1091AC": "10", "5623EJ": "56"},
        ),
        _build(
            "age_boundary",
            "ward_overview",
            "normal",
            [
                "Meneer ",
                ("Janssen", EntityType.NAME),
                " (",
                ("89 jaar", EntityType.AGE),
                ") en mevrouw ",
                ("De Boer", EntityType.NAME),
                " (",
                ("95 jaar", EntityType.AGE),
                ") liggen op dezelfde afdeling.",
            ],
            {"95 jaar": "90+ jaar"},
        ),
        _build(
            "invalid_bsn_negative",
            "logistics",
            "adversarial",
            [
                # 9-cijferig getal dat de elfproef faalt → mag GEEN BSN-entity worden.
                f"Het ordernummer van de leverancier is {INVALID_BSN} en de levering volgt.",
            ],
        ),
        _build(
            "address_gap",
            "patient_intro",
            "adversarial",
            [
                # ADDRESS heeft geen detector → moet als structurele FN/lek opvallen.
                "De patiënt woont aan de ",
                ("Dorpsstraat 12", EntityType.ADDRESS),
                " te ",
                ("Zwolle", EntityType.LOCATION),
                ".",
            ],
        ),
        _build(
            "name_looks_like_org",
            "clinical_note",
            "adversarial",
            [
                "Dhr. ",
                ("Bakker", EntityType.NAME),
                " bezocht vandaag de huisartsenpost.",
            ],
        ),
        _build(
            "diagnosis_freetext_gap",
            "clinical_note",
            "adversarial",
            [
                # DIAGNOSIS heeft geen detector → structurele FN (clinical_sensitive).
                "De diagnose luidt ",
                ("amyotrofische laterale sclerose", EntityType.DIAGNOSIS),
                " volgens de neuroloog.",
            ],
        ),
    ]


def main() -> None:
    records = build_records()
    report = validate_dataset(records)
    if not report.ok:
        raise SystemExit("Bootstrap-dataset is ongeldig:\n" + "\n".join(report.errors))

    dump_jsonl(records, DATASET_FILE)
    write_manifest(
        DATASET_FILE,
        version=DATASET_VERSION,
        seed=0,
        generator="bootstrap.py (offline, deterministisch)",
        record_count=len(records),
        extra={"warnings": report.warnings, "entity_count": report.entity_count},
    )
    print(  # noqa: T201 — CLI-feedback
        f"Bootstrap-dataset geschreven: {DATASET_FILE} "
        f"({len(records)} records, {report.entity_count} entities)"
    )


if __name__ == "__main__":
    main()
