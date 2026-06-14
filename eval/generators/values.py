"""Fictieve, geldige waarden voor placeholder-substitutie.

De LLM levert het narratief met getypeerde placeholders; hier vullen we de
concrete waarden in. Structurele identifiers (BSN, IBAN) worden altijd lokaal
gegenereerd met geldige checksums, zodat de ground truth gegarandeerd correct
is en 100% fictief blijft. Vrije-tekst-types (NAME, ORG, ...) gebruiken de
door de LLM gekozen inhoud, of vallen terug op een fictieve pool.

Alle randomness loopt via een meegegeven `random.Random` zodat dezelfde seed
dezelfde dataset oplevert (reproduceerbaarheid, TESTPLAN.md §5).
"""

from __future__ import annotations

import random

from data.icd10_rare import RARE_ICD10_CODES
from shared.crypto import validate_bsn_elfproef, validate_iban_checksum
from shared.models import EntityType

_SURNAMES = (
    "Pietersen", "Janssen", "De Vries", "Bakker", "Visser", "Smit", "Meijer",
    "Mulder", "De Boer", "Bos", "Vos", "Peters", "Hendriks", "Van Dijk",
)
_FIRST_NAMES = ("Anna", "Jan", "Sanne", "Pieter", "Fatima", "Mohammed", "Lotte", "Kees")
_ORGS = (
    "OLVG", "Catharina Ziekenhuis", "Radboudumc", "Martini Ziekenhuis",
    "Isala Klinieken", "Maxima Medisch Centrum", "Rijnstate",
)
_CITIES = (
    "Deventer", "Amsterdam", "Eindhoven", "Heerlen", "Zwolle", "Groningen",
    "Nijmegen", "Tilburg", "Arnhem", "Maastricht",
)
_STREETS = ("Dorpsstraat", "Kerkweg", "Molenlaan", "Schoolstraat", "Beukenlaan", "Julianastraat")
_DIAGNOSES = (
    "astma bronchiale", "diabetes mellitus type 2", "hypertensie",
    "amyotrofische laterale sclerose", "reumatoide artritis", "COPD",
)
_PRODUCTS = ("Metformine", "Salbutamol", "Atorvastatine", "insulinepomp", "pacemaker")
_COMMON_ICD = ("J45.0", "E11.9", "I10", "M05.9", "J44.9")
_EMAIL_DOMAINS = ("voorbeeld.nl", "fictiefmail.nl", "testdomein.nl")

# Types waarvoor we de LLM-gekozen waarde respecteren (indien aanwezig).
_PROVIDED_OK = frozenset(
    {
        EntityType.NAME,
        EntityType.ORG,
        EntityType.LOCATION,
        EntityType.DIAGNOSIS,
        EntityType.PRODUCT,
        EntityType.PROJECT,
        EntityType.ICD10_CODE,
        EntityType.AGE,
        EntityType.ADDRESS,
    }
)
# Types die we altijd lokaal genereren (checksum-garantie), provided wordt genegeerd.
_AUTO_ALWAYS = frozenset({EntityType.BSN, EntityType.IBAN})


def _gen_bsn(rng: random.Random) -> str:
    for _ in range(1000):
        candidate = f"{rng.randint(0, 999_999_999):09d}"
        if validate_bsn_elfproef(candidate):
            return candidate
    raise RuntimeError("Geen geldige BSN kunnen genereren")  # pragma: no cover


def _gen_iban(rng: random.Random) -> str:
    bank = "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4))
    account = f"{rng.randint(0, 9_999_999_999):010d}"
    bban = bank + account
    converted = "".join(
        str(ord(c) - ord("A") + 10) if c.isalpha() else c for c in (bban + "NL00")
    )
    check = 98 - (int(converted) % 97)
    iban = f"NL{check:02d}{bban}"
    if not validate_iban_checksum(iban):  # pragma: no cover - defensief
        raise RuntimeError(f"Gegenereerd IBAN {iban} faalt mod-97")
    return iban


def _gen_phone(rng: random.Random) -> str:
    return "06-" + "".join(str(rng.randint(0, 9)) for _ in range(8))


def _gen_email(rng: random.Random) -> str:
    surname = rng.choice(_SURNAMES).split()[-1].lower()
    return f"{surname}@{rng.choice(_EMAIL_DOMAINS)}"


def _gen_postcode(rng: random.Random) -> str:
    letters = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2))
    return f"{rng.randint(1000, 9999)}{letters}"


def _gen_date(rng: random.Random, year_lo: int, year_hi: int) -> str:
    day = rng.randint(1, 28)
    month = rng.randint(1, 12)
    year = rng.randint(year_lo, year_hi)
    return f"{day:02d}-{month:02d}-{year}"


def _gen_birthdate(rng: random.Random) -> str:
    return _gen_date(rng, 1935, 2006)


def _gen_treatment_date(rng: random.Random) -> str:
    return _gen_date(rng, 2019, 2025)


def _gen_mrn(rng: random.Random) -> str:
    return "MRN" + "".join(str(rng.randint(0, 9)) for _ in range(7))


def _gen_epd(rng: random.Random) -> str:
    return "EPD-" + "".join(str(rng.randint(0, 9)) for _ in range(6))


def _gen_kenteken(rng: random.Random) -> str:
    letters = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(2))
    return f"{rng.randint(10, 99)}-{letters}-{rng.randint(100, 999)}"


def _gen_age(rng: random.Random) -> str:
    # Af en toe boven de 90-grens zodat BR-B03 getriggerd wordt.
    age = rng.choice([rng.randint(18, 89), rng.randint(90, 101)])
    return f"{age} jaar"


def _gen_icd(rng: random.Random) -> str:
    pool = list(_COMMON_ICD) + list(RARE_ICD10_CODES)
    return rng.choice(pool)


def _gen_address(rng: random.Random) -> str:
    return f"{rng.choice(_STREETS)} {rng.randint(1, 199)}"


def _gen_project(rng: random.Random) -> str:
    return f"{rng.choice(('ONCO', 'CARD', 'NEURO'))}-{rng.randint(100, 9999)}"


_GENERATORS = {
    EntityType.BSN: _gen_bsn,
    EntityType.IBAN: _gen_iban,
    EntityType.PHONE: _gen_phone,
    EntityType.EMAIL: _gen_email,
    EntityType.POSTCODE_PC6: _gen_postcode,
    EntityType.BIRTHDATE: _gen_birthdate,
    EntityType.ADMISSION_DATE: _gen_treatment_date,
    EntityType.DISCHARGE_DATE: _gen_treatment_date,
    EntityType.EXAM_DATE: _gen_treatment_date,
    EntityType.MRN: _gen_mrn,
    EntityType.EPD_ID: _gen_epd,
    EntityType.KENTEKEN: _gen_kenteken,
    EntityType.AGE: _gen_age,
    EntityType.ICD10_CODE: _gen_icd,
    EntityType.ADDRESS: _gen_address,
}

_POOLS = {
    EntityType.NAME: lambda rng: rng.choice(_SURNAMES),
    EntityType.ORG: lambda rng: rng.choice(_ORGS),
    EntityType.LOCATION: lambda rng: rng.choice(_CITIES),
    EntityType.DIAGNOSIS: lambda rng: rng.choice(_DIAGNOSES),
    EntityType.PRODUCT: lambda rng: rng.choice(_PRODUCTS),
    EntityType.PROJECT: _gen_project,
}


def generate_value(etype: EntityType, provided: str | None, rng: random.Random) -> str:
    """Lever een fictieve waarde voor een placeholder.

    - BSN/IBAN: altijd lokaal (checksum-garantie).
    - Vrije-tekst-types: LLM-waarde indien aanwezig, anders pool.
    - Overige structurele types: lokale generator.
    """
    if etype in _AUTO_ALWAYS:
        return _GENERATORS[etype](rng)
    if provided and etype in _PROVIDED_OK:
        return provided.strip()
    if etype in _GENERATORS:
        return _GENERATORS[etype](rng)
    if etype in _POOLS:
        return _POOLS[etype](rng)
    # Laatste redmiddel: gebruik de provided waarde of een neutrale tekst.
    return (provided or "onbekend").strip()
