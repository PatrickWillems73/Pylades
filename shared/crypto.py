"""HMAC-pseudoniemen en checksum-validatoren (BR-C01 + BR-A02-validators).

Bewust geen afhankelijkheid op `config` of `db`: alle pseudonimisering-
logica blijft herbruikbaar voor CLI-tooling en triviaal te auditen in
één bestand. `load_or_create_secret` en `rotate_global_secret` zijn de
enige I/O-functies hier.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from pathlib import Path

from shared.models import SHORT_TYPE_CODES, EntityType

_GLOBAL_SECRET_BYTES = 32
_PSEUDONYM_HEX_CHARS = 6


def load_or_create_secret(secret_path: Path) -> bytes:
    """Laad of genereer de 32-byte globale HMAC-sleutel.

    Bij eerste run: `secrets.token_bytes(32)` -> file met POSIX-mode 0o600.
    Daarna: alleen lezen. Parent-directory wordt aangemaakt als hij ontbreekt.

    Race: bij twee gelijktijdige eerste runs kan één instance overschrijven.
    In v0.3 (één gebruiker, één machine) accepteren we dat; v1.0 doet dit
    via een dedicated init-CLI vóór de proxy start.
    """
    if secret_path.exists():
        return secret_path.read_bytes()

    key = secrets.token_bytes(_GLOBAL_SECRET_BYTES)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_bytes(key)
    secret_path.chmod(0o600)
    return key


def rotate_global_secret(secret_path: Path) -> Path:
    """Archiveer huidige sleutel en genereer een nieuwe (BR-C01 / Config-pagina).

    De bestaande sleutel gaat naar `<secret_path>.archived-<UTC-ts>` met
    mode `0o600`; een verse 32-byte sleutel komt op het oorspronkelijke
    pad te staan. Returnt het archive-pad. Bestaat `secret_path` nog niet,
    dan wordt gewoon een nieuwe sleutel gegenereerd en wordt een leeg
    archive met dezelfde permissies weggeschreven — zo blijft de
    contract-belofte "elke rotatie levert een archive" symmetrisch.

    De caller is verantwoordelijk voor het invalideren van bestaande
    pseudoniem-mappings (UI-flow: CSV-export aanbieden vóór rotatie).
    """
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = secret_path.with_name(f"{secret_path.name}.archived-{timestamp}")

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        archive_path.write_bytes(secret_path.read_bytes())
    else:
        archive_path.write_bytes(b"")
    archive_path.chmod(0o600)

    new_key = secrets.token_bytes(_GLOBAL_SECRET_BYTES)
    secret_path.write_bytes(new_key)
    secret_path.chmod(0o600)
    return archive_path


def derive_session_key(global_secret: bytes, session_id: str) -> bytes:
    """Leid een sessie-scoped sleutel af van de globale sleutel.

    Door per sessie een eigen key te derive-en, levert dezelfde originele
    waarde in twee verschillende sessies twee verschillende pseudoniemen op
    — zonder dat we voor elke sessie de globale sleutel hoeven roteren.
    """
    return hmac.new(global_secret, session_id.encode(), hashlib.sha256).digest()


def make_pseudonym(session_key: bytes, original: str, entity_type: EntityType) -> str:
    """Bereken het pseudoniem voor `original` binnen een sessie.

    Format: `[XXX-aaaaaa]` waarbij `XXX` de 3-letter type-code is en
    `aaaaaa` de eerste 6 hex-chars van HMAC-SHA-256(session_key, payload).

    De payload `f"{type}:{original.strip()}"` bevat het type zodat dezelfde
    string als verschillend type (bv. `"Jan"` als NAME vs ORG) verschillende
    pseudoniemen krijgt; `strip()` voorkomt cosmetische mismatches.
    """
    payload = f"{entity_type.value}:{original.strip()}".encode()
    digest = hmac.new(session_key, payload, hashlib.sha256).hexdigest()
    short_type = SHORT_TYPE_CODES[entity_type]
    return f"[{short_type}-{digest[:_PSEUDONYM_HEX_CHARS]}]"


def validate_bsn_elfproef(bsn: str) -> bool:
    """Valideer 9-cijferig BSN via de elfproef.

    Gewichten (9, 8, 7, 6, 5, 4, 3, 2, -1); som mod 11 == 0 én som != 0.
    Strings met leading zero blijven valid: regex `\\b\\d{9}\\b` levert
    altijd 9 chars aan, dus `len == 9 and isdigit()` is de complete check.
    """
    if len(bsn) != 9 or not bsn.isdigit():
        return False
    weights = (9, 8, 7, 6, 5, 4, 3, 2, -1)
    total = sum(int(digit) * weight for digit, weight in zip(bsn, weights, strict=True))
    return total != 0 and total % 11 == 0


def validate_iban_checksum(iban: str) -> bool:
    """Valideer een IBAN via mod-97 (ISO 13616).

    Algoritme: verplaats de eerste 4 chars naar het eind, vervang elke
    letter door tweecijferig nummer (A=10..Z=35), interpreteer als geheel
    getal, en check `% 97 == 1`. Werkt voor alle landen, niet alleen NL.
    Spaties en hoofdletter-/kleinletter-verschillen worden genegeerd.
    """
    iban = iban.replace(" ", "").upper()

    # Minimum-format: 2 letters (landcode) + 2 cijfers (checksum) + 1+ char.
    if len(iban) < 5 or not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False

    rearranged = iban[4:] + iban[:4]

    numeric_parts: list[str] = []
    for char in rearranged:
        if char.isdigit():
            numeric_parts.append(char)
        elif "A" <= char <= "Z":
            numeric_parts.append(str(ord(char) - ord("A") + 10))
        else:
            return False

    return int("".join(numeric_parts)) % 97 == 1
