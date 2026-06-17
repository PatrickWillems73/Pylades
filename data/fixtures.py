"""Fictieve Nederlandse scenario's voor de complete pijplijn.

Acht prompts die samen elke detectielaag, alle vijf generalisering-regels
(BR-B01..B05) en de review-queue raken. Geen `pytest.fixture` maar plain
immutable dataklassen, zodat dezelfde set bruikbaar is voor automated
tests én voor handmatige demo-runs vanuit de Streamlit-UI.

Bij import draait een consistency-check: VALID_BSN moet de elfproef
passeren, INVALID_BSN moet er door zakken, en zeldzame ICD-codes die in
prompts genoemd worden moeten ook echt in `RARE_ICD10_CODES` staan. Zo
worden tests in stap 5+ niet stilzwijgend onvolledig wanneer iemand een
constante wijzigt.
"""

from dataclasses import dataclass
from typing import Final

from data.icd10_rare import RARE_ICD10_CODES
from shared.crypto import validate_bsn_elfproef, validate_iban_checksum

# Bekende geldige + ongeldige BSN's. Beide ook letterlijk in fixtures
# gebruikt, maar als constantes geëxporteerd zodat tests ze rechtstreeks
# in unit-checks kunnen gebruiken zonder de prompt te parsen.
VALID_BSN: Final[str] = "123456782"
INVALID_BSN: Final[str] = "123456789"

# Geldig NL-IBAN (Mod-97 klopt); ook gebruikt in `mixed_contact_details`.
VALID_NL_IBAN: Final[str] = "NL91ABNA0417164300"


@dataclass(frozen=True, slots=True)
class Fixture:
    """Eén fictief test-scenario.

    Velden:
    - `id`: korte unieke string voor lookup in tests.
    - `description`: in één zin wat dit scenario test.
    - `prompt`: tekst zoals een gebruiker hem in Pylades zou plakken.
    - `notes`: vrije aantekening over wat verwacht wordt — informatief,
      niet machine-gecontroleerd. Echte assertions horen in de test
      die deze fixture gebruikt.
    """

    id: str
    description: str
    prompt: str
    notes: str = ""


FIXTURES: Final[tuple[Fixture, ...]] = (
    Fixture(
        id="basic_patient_snippet",
        description="Standaard patiënt-introductie met BSN, postcode, geboortedatum.",
        prompt=(
            f"Mevrouw Pietersen, BSN {VALID_BSN}, woont op postcode 7411AB in "
            "Deventer. Ze is geboren op 03-04-1972."
        ),
        notes=(
            "Regex: BSN (passeert elfproef), POSTCODE_PC6, BIRTHDATE. "
            "DEDUCE: NAME ('Pietersen'), LOCATION ('Deventer'). "
            "Generalisering: BIRTHDATE -> BIRTH_YEAR (1972); PC6 -> PC2 (74)."
        ),
    ),
    Fixture(
        id="clinical_note_mrn_epd",
        description="Klinische notitie met MRN, EPD-id en opname-context (BR-B04).",
        prompt=(
            "Patiënt met MRN1234567 en EPD-789012 werd opgenomen op 15-03-2024 "
            "met diagnose J45.0 (astma bronchiale). Pulmonale functietest gepland."
        ),
        notes=(
            "Regex: MRN, EPD_ID, ICD10_CODE (J45.0, niet zeldzaam). "
            "Generalisering: ADMISSION_DATE met contextwoord 'opgenomen op' "
            "-> '2024-03'. J45.0 mag NIET naar review (komt niet voor in RARE_ICD10_CODES)."
        ),
    ),
    Fixture(
        id="rare_icd_to_review",
        description="Zeldzame ICD-10 G71.0 die naar de review-queue moet (BR-B05).",
        prompt=(
            "Genetisch onderzoek bevestigt diagnose G71.0 bij betrokkene. "
            "Verwijzing naar het neuromusculair expertisecentrum."
        ),
        notes=(
            "Regex: ICD10_CODE (G71.0). "
            "Review-routing: G71.0 zit in RARE_ICD10_CODES -> flag voor manual review."
        ),
    ),
    Fixture(
        id="invalid_bsn_negative",
        description="Negatieve test: 9-cijferig getal dat regex matched maar elfproef faalt.",
        prompt=(
            f"Het ordernummer van de leverancier is {INVALID_BSN} en de "
            "verwachte leverdatum is volgende week donderdag."
        ),
        notes=(
            f"De regex `\\b\\d{{9}}\\b` matched {INVALID_BSN}, maar "
            "validate_bsn_elfproef returnt False -> mag NIET als BSN-entity "
            "in confident_entities of pending_review verschijnen."
        ),
    ),
    Fixture(
        id="age_boundary_above_and_below",
        description="Leeftijden net onder en boven de 90-grens (BR-B03).",
        prompt=(
            "Twee patiënten op afdeling: meneer Janssen (89 jaar) blijft als "
            "89 staan, en mevrouw De Boer (95 jaar) moet als 90+ verschijnen."
        ),
        notes=(
            "Generalisering: AGE 89 -> blijft '89 jaar'; AGE 95 -> '90+ jaar'. "
            "DEDUCE detecteert 'Janssen' en 'De Boer' als NAME (laag-confidence "
            "mogelijk voor 'De Boer' -> review-queue-kandidaat afhankelijk van threshold)."
        ),
    ),
    Fixture(
        id="mixed_contact_details",
        description="Vrije tekst met meerdere direct-identifiers in één zin.",
        prompt=(
            f"Stuur de factuur naar pietersen@voorbeeld.nl, IBAN {VALID_NL_IBAN}, "
            "telefoon 06-12345678. De bedrijfsauto heeft kenteken 12-AB-345."
        ),
        notes=(
            "Regex: EMAIL, IBAN (mod-97 klopt), PHONE, KENTEKEN. "
            "Test dat 4 entity-types in één prompt allemaal binnenkomen."
        ),
    ),
    Fixture(
        id="org_location_pc6_pair",
        description="Organisatie + locatie + twee verschillende PC6-postcodes (BR-B02).",
        prompt=(
            "OLVG Amsterdam (postcode 1091AC) heeft overleg met het Catharina "
            "Ziekenhuis in Eindhoven (5623EJ) over een gezamenlijk traject."
        ),
        notes=(
            "DEDUCE: ORG ('OLVG', 'Catharina Ziekenhuis'), LOCATION ('Amsterdam', "
            "'Eindhoven'). Regex: POSTCODE_PC6 -> generalisering levert '10' en '56'."
        ),
    ),
    Fixture(
        id="rare_icd_with_quasi_identifier_stack",
        description="Zeldzame ICD + meerdere quasi-identifiers (BR-A01 categorie-mix).",
        prompt=(
            "Onderzoeker analyseert cohort: vrouw, geboortejaar 1958, woonachtig "
            "in Heerlen, diagnose E70.0. Vervolgafspraak bij metabolisch centrum."
        ),
        notes=(
            "Regex: ICD10_CODE (E70.0, zeldzaam -> review). "
            "DEDUCE: LOCATION ('Heerlen'). "
            "Mix laat zien dat CLINICAL_SENSITIVE naast QUASI_IDENTIFIER kan staan."
        ),
    ),
)


def get_fixture(fixture_id: str) -> Fixture:
    """Pak één fixture op id; raise `KeyError` als hij niet bestaat."""
    for fixture in FIXTURES:
        if fixture.id == fixture_id:
            return fixture
    raise KeyError(f"Geen fixture met id {fixture_id!r}")


def _check_fixture_consistency() -> None:
    # ID-uniqueness: anders zou `get_fixture()` stilletjes de eerste pakken
    # en lijken tests andere data te krijgen dan ze denken.
    ids = [fixture.id for fixture in FIXTURES]
    duplicates = {fid for fid in ids if ids.count(fid) > 1}
    if duplicates:
        raise RuntimeError(f"FIXTURES heeft dubbele id's: {sorted(duplicates)}")

    # Crypto-constanten kloppen met de echte validators.
    if not validate_bsn_elfproef(VALID_BSN):
        raise RuntimeError(f"VALID_BSN={VALID_BSN!r} zou de elfproef moeten passeren")
    if validate_bsn_elfproef(INVALID_BSN):
        raise RuntimeError(f"INVALID_BSN={INVALID_BSN!r} mag de elfproef NIET passeren")
    if not validate_iban_checksum(VALID_NL_IBAN):
        raise RuntimeError(f"VALID_NL_IBAN={VALID_NL_IBAN!r} zou mod-97 moeten passeren")

    # Zeldzame ICD-codes die in prompts genoemd worden, moeten ook echt
    # in RARE_ICD10_CODES staan. Anders zou een fixture rood draaien om
    # de verkeerde reden (G71.0 niet als rare gedetecteerd -> review-queue
    # gemist) en zou de oorzaak ver weg liggen.
    rare_codes_in_fixtures = {"G71.0", "E70.0"}
    missing = rare_codes_in_fixtures - RARE_ICD10_CODES
    if missing:
        raise RuntimeError(
            f"ICD-codes uit fixtures ontbreken in RARE_ICD10_CODES: {sorted(missing)}"
        )


_check_fixture_consistency()
