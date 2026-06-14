"""LLM-gedreven synthetische dossier-generator met span-level ground truth.

Aanpak (TESTPLAN.md §5): de LLM schrijft realistische NL-klinische tekst met
*getypeerde placeholders* (`{{BSN}}`, `{{NAME:Pietersen}}`, `opgenomen op
{{ADMISSION_DATE}}`). Wij renderen die placeholders met fictieve, geldige
waarden en berekenen de offsets lokaal. Zo is de ground truth gegarandeerd
correct (geen LLM-offset-giswerk) en zijn BSN/IBAN checksum-geldig.

De LLM bepaalt narratief, entity-plaatsing en vrije-tekst-inhoud; wij bepalen
de concrete waarden en de labels.
"""

from __future__ import annotations

import logging
import math
import random
import re

from eval.generators import llm_client
from eval.generators.values import generate_value
from eval.schema import EvalRecord, GoldEntity
from eval.validators import validate_dataset
from shared.models import EntityType

logger = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*(?::\s*([^}]*?))?\s*\}\}")

# Synoniemen die de LLM kan kiezen → canonieke EntityType-naam.
_ALIASES = {
    "PERSON": "NAME",
    "PER": "NAME",
    "GPE": "LOCATION",
    "LOC": "LOCATION",
    "POSTCODE": "POSTCODE_PC6",
    "ZIP": "POSTCODE_PC6",
    "DOB": "BIRTHDATE",
}

_AUTO_TYPES = (
    "BSN", "IBAN", "PHONE", "EMAIL", "POSTCODE_PC6", "BIRTHDATE",
    "MRN", "EPD_ID", "KENTEKEN", "ADMISSION_DATE", "DISCHARGE_DATE", "EXAM_DATE",
)
_VALUE_TYPES = ("NAME", "ORG", "LOCATION", "DIAGNOSIS", "PRODUCT", "AGE", "ICD10_CODE", "ADDRESS")

_SYSTEM = (
    "Je bent een datagenerator voor het testen van een Nederlandse "
    "pseudonimisatie-tool in de zorg. Je schrijft realistische, volledig "
    "FICTIEVE Nederlandse klinische dossierteksten. Gebruik nooit echte "
    "persoonsgegevens. Markeer gevoelige entiteiten met placeholders."
)

_PLACEHOLDER_INSTRUCTIONS = (
    "Gebruik placeholders in dubbele accolades voor gevoelige gegevens.\n"
    "- Laat deze types LEEG (wij vullen geldige waarden in): "
    + ", ".join(f"{{{{{t}}}}}" for t in _AUTO_TYPES)
    + "\n- Geef bij deze types zelf fictieve inhoud mee na een dubbele punt, "
    "bv. {{NAME:De Vries}}, {{ORG:Radboudumc}}, {{LOCATION:Utrecht}}, "
    "{{DIAGNOSIS:astma bronchiale}}, {{AGE:92 jaar}}, {{ICD10_CODE:J45.0}}, "
    f"{{ADDRESS:Kerkweg 12}}: {', '.join(_VALUE_TYPES)}\n"
    "- Plaats behandeldatums achter een contextwoord, bv. 'opgenomen op "
    "{{ADMISSION_DATE}}', 'ontslagen op {{DISCHARGE_DATE}}', 'MRI op {{EXAM_DATE}}'.\n"
    "- Output UITSLUITEND de dossiertekst, geen uitleg, geen opsomming."
)

_SCENARIOS = (
    "Opnamebrief van de afdeling interne geneeskunde.",
    "Verwijsbrief van de huisarts naar een specialist.",
    "Klinische notitie met laboratoriumuitslagen.",
    "Ontslagbrief met medicatieoverzicht.",
    "Multidisciplinair overleg over een patient.",
    "Polikliniek-verslag van een controleafspraak.",
    "Radiologieverslag met onderzoeksdatum.",
)

_ADVERSARIAL = (
    "Schrijf een dossier met lastige randgevallen: gebruik ergens een "
    "9-cijferig ORDERNUMMER of dossiernummer als gewone cijfers ZONDER "
    "placeholder (dit is GEEN BSN), gebruik een achternaam die ook een "
    "organisatie of beroep zou kunnen zijn (met {{NAME:...}}), noem een datum "
    "zonder contextwoord, en gebruik minstens een buitenlandse naam. Zorg voor "
    "een hoge dichtheid aan entiteiten."
)


def _canonical_type(raw: str) -> EntityType | None:
    name = _ALIASES.get(raw.upper(), raw.upper())
    return EntityType.__members__.get(name)


def _birthyear(value: str) -> str | None:
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", value.strip())
    return m.group(3) if m else None


def _year_month(value: str) -> str | None:
    m = re.match(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", value.strip())
    return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}" if m else None


def _age_general(value: str) -> str | None:
    m = re.search(r"\d+", value)
    if m and int(m.group()) >= 90:
        return "90+ jarige" if "jarige" in value.lower() else "90+ jaar"
    return None


def _expected_generalization(etype: EntityType, value: str) -> str | None:
    if etype is EntityType.BIRTHDATE:
        return _birthyear(value)
    if etype is EntityType.POSTCODE_PC6:
        digits = re.sub(r"\D", "", value)
        return digits[:2] if len(digits) >= 2 else None
    if etype is EntityType.AGE:
        return _age_general(value)
    if etype in (EntityType.ADMISSION_DATE, EntityType.DISCHARGE_DATE, EntityType.EXAM_DATE):
        return _year_month(value)
    return None


def render(
    template_text: str,
    *,
    rid: str,
    scenario: str,
    difficulty: str,
    rng: random.Random,
    model: str,
    seed: int = 0,
) -> EvalRecord:
    """Vervang placeholders door fictieve waarden en bouw de ground truth."""
    parts: list[str] = []
    entities: list[GoldEntity] = []
    expected: dict[str, str] = {}
    cursor = 0
    last = 0

    for match in _PLACEHOLDER_RE.finditer(template_text):
        literal = template_text[last : match.start()]
        parts.append(literal)
        cursor += len(literal)

        etype = _canonical_type(match.group(1))
        provided = match.group(2)
        if etype is None:
            # Onbekend type: behandel als platte tekst (geen entity).
            fallback = (provided or match.group(1)).strip()
            logger.warning("Onbekend placeholder-type %r in %s", match.group(1), rid)
            parts.append(fallback)
            cursor += len(fallback)
            last = match.end()
            continue

        value = generate_value(etype, provided, rng)
        entities.append(
            GoldEntity(start=cursor, end=cursor + len(value), text=value, type=etype)
        )
        gen = _expected_generalization(etype, value)
        if gen is not None:
            expected[value] = gen
        parts.append(value)
        cursor += len(value)
        last = match.end()

    parts.append(template_text[last:])
    prompt = "".join(parts)
    if "{{" in prompt or "}}" in prompt:
        raise ValueError(f"{rid}: niet alle placeholders gerenderd: {prompt!r}")

    return EvalRecord(
        id=rid,
        prompt=prompt,
        entities=entities,
        seed=seed,
        scenario=scenario,
        difficulty=difficulty,
        expected_generalization=expected,
        meta={"source": "synthetic", "model": model},
    )


def _user_prompt(scenario: str, difficulty: str) -> str:
    base = f"Schrijf een fictief Nederlands patientdossier. Scenario: {scenario}\n\n"
    if difficulty == "adversarial":
        base += _ADVERSARIAL + "\n\n"
    base += _PLACEHOLDER_INSTRUCTIONS
    return base


def generate_dataset(
    n: int,
    *,
    model: str | None = None,
    seed: int = 1,
    adversarial_fraction: float = 0.3,
    max_retries: int = 3,
) -> list[EvalRecord]:
    """Genereer `n` gevalideerde records via de Anthropic-API."""
    chosen_model = model or llm_client.discover_model()
    n_adversarial = math.ceil(n * adversarial_fraction)
    records: list[EvalRecord] = []

    for i in range(n):
        difficulty = "adversarial" if i >= n - n_adversarial else "normal"
        scenario = (
            _ADVERSARIAL[:40] if difficulty == "adversarial" else _SCENARIOS[i % len(_SCENARIOS)]
        )
        scenario_label = "adversarial" if difficulty == "adversarial" else scenario
        rid = f"syn_{i:03d}"

        record = _generate_one(
            rid=rid,
            scenario=scenario_label,
            difficulty=difficulty,
            model=chosen_model,
            seed=seed + i,
            max_retries=max_retries,
        )
        records.append(record)
        logger.info("Gegenereerd %s (%s): %d entities", rid, difficulty, len(record.entities))

    return records


def _generate_one(
    *,
    rid: str,
    scenario: str,
    difficulty: str,
    model: str,
    seed: int,
    max_retries: int,
) -> EvalRecord:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        rng = random.Random(seed)
        text = llm_client.complete(
            model=model,
            system=_SYSTEM,
            user=_user_prompt(scenario if difficulty != "adversarial" else "lastige randgevallen",
                              difficulty),
        )
        try:
            record = render(
                text,
                rid=rid,
                scenario=scenario,
                difficulty=difficulty,
                rng=rng,
                model=model,
                seed=seed,
            )
        except ValueError as exc:
            last_error = exc
            logger.warning("%s render-fout (poging %d): %s", rid, attempt + 1, exc)
            continue

        report = validate_dataset([record])
        if report.ok:
            return record
        last_error = ValueError("; ".join(report.errors))
        logger.warning("%s validatie-fout (poging %d): %s", rid, attempt + 1, report.errors)

    raise RuntimeError(
        f"{rid}: kon na {max_retries} pogingen geen geldig record maken: {last_error}"
    )
