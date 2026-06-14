"""Context-gebaseerde NAME-detectie voor zorgrollen en relatie-aanduidingen.

DEDUCE mist in adversariële dossiers vaak achternamen die expliciet achter een
rol staan (`Verpleegkundig specialist: Okonkwo`, `partner Olufemi Adeyemi`,
`dochter, Petrova`). Deze heuristiek vult NAME-spans aan; regex blijft leidend
bij overlap in de pijplijn-runner. Bedoeld als lichtgewicht alternatief voor
een GLiNER NAME-fallback in eval/productie.
"""

from __future__ import annotations

import re

# Eén naamwoord: begint met hoofdletter (Unicode), geen lowercase ASCII aanstart.
_NAME_WORD = r"(?-i:[^\W\d_a-z][\w'-]*)"
# Optioneel tweede woord (alleen als ook met hoofdletter begint).
_NAME = rf"({_NAME_WORD}(?:[ \t]+{_NAME_WORD})?)"

# Titel/rol + optionele titel + naam (tot komma, punt of regeleinde).
_LABEL_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"verpleegkundig\s+specialist|physician\s+assistant|verwijz(?:er|end)\s+huisarts|"
    rf"behandelend(?:\s+(?:internist|specialist))?|hoofdbehandelaar|"
    rf"verwijzer(?:\s+\([^)]+\))?|huisarts|arts-assistent|coassistent"
    rf")\s*:\s*"
    rf"(?:(?:dr\.|drs\.|dhr\.|mevrouw|mevr\.)\s+)?"
    rf"{_NAME}"
)

# Rolwoord direct vóór een naam (zonder dubbele punt). Geen `internist` — dat
# botst met `internist dr. Naam` (titel vóór echte achternaam). Geen losse
# `diëtist` — botst met `Diëtist Timmerman consulteerde` (achternaam ≠ rol).
_ROLE_BEFORE_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"consulent(?:\s+\w+)?|fysiotherapeut|apotheker|"
    rf"arts-assistent|coassistent|cardioloog|verpleegkundig\s+specialist"
    rf")\s+"
    rf"{_NAME}"
)

# `Verwijzing diëtist Mwangi` (expliciete verwijsregel, niet algemeen diëtist-label).
_REFERRAL_DIETIST = re.compile(
    rf"(?i)\bverwijzing\s+di[eë]tist(?:e)?\s+({_NAME_WORD})"
)

# Familierelatie / contactpersoon (direct na relatie).
_RELATION_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"partner|dochter|zoon|schoonzoon|echtgeno(?:o)?t(?:e)?|moeder|vader|broer|zus|contactpersoon"
    rf")\s+"
    rf"(?:(?:mevrouw|dhr\.)\s+)?"
    rf"{_NAME}"
)

# Relatie + komma + naam (`dochter, Petrova`; `echtgenote, mevrouw Koningin`).
_RELATION_COMMA_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"partner|dochter|zoon|schoonzoon|echtgeno(?:o)?t(?:e)?|moeder|vader|broer|zus|contactpersoon"
    rf")\s*,\s*"
    rf"(?:(?:mevrouw|dhr\.)\s+)?"
    rf"{_NAME}"
)

# `Fysiotherapie door Mwangi` (beroep als zelfstandig naamwoord + door).
_THROUGH_NAME = re.compile(
    rf"(?i)\b(?:fysiotherapie|physiotherapie|ergotherapie|logopedie)\s+door\s+({_NAME_WORD})\b"
)

# `naar huisarts Okonkwo en` (zonder dubbele punt). Sla tussenvoegsel over
# (`huisarts De Dokter` → `_HUISARTS_DE_NAME`).
_HUISARTS_SURNAME = re.compile(
    rf"(?i)\bhuisarts\s+({_NAME_WORD})(?![ \t]+{_NAME_WORD})\b"
)

# Adversariële achternaam die op een beroep lijkt (`huisarts De Dokter`).
_HUISARTS_DE_NAME = re.compile(
    rf"(?i)\b(?:verwezen\s+door\s+)?huisarts\s+{_NAME}"
)

# Achternaam = beroepswoord (`behandeld door Dokter, cardioloog`).
_PROFESSION_SURNAME = re.compile(
    rf"(?i)\bbehandeld\s+door\s+({_NAME_WORD}),\s*"
    rf"(?:cardioloog|internist|chirurg|patholoog|huisarts|oncolog(?:ie|oloog)?)"
)

# Afkorting in lopende tekst (`i.o.m. Okonkwo`).
_IOM_NAME = re.compile(rf"(?i)\bi\.o\.m\.\s+{_NAME}")

# Engelse rol zonder dubbele punt (`physician assistant Andersson`).
_PHYSICIAN_ASSISTANT = re.compile(rf"(?i)\bphysician\s+assistant\s+{_NAME}")

_ROLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    _LABEL_NAME,
    _ROLE_BEFORE_NAME,
    _REFERRAL_DIETIST,
    _RELATION_NAME,
    _RELATION_COMMA_NAME,
    _THROUGH_NAME,
    _HUISARTS_DE_NAME,
    _HUISARTS_SURNAME,
    _PROFESSION_SURNAME,
    _IOM_NAME,
    _PHYSICIAN_ASSISTANT,
)


def _looks_like_proper_name(surface: str) -> bool:
    """Extra filter: elke naam-component start met een hoofdletter."""
    parts = surface.split()
    if not parts or len(parts) > 4:
        return False
    return all(part and part[0].isupper() for part in parts)


def detect_role_context_name_spans(text: str) -> list[tuple[int, int, str]]:
    """Return `(start, end, surface)` voor context-gedetecteerde namen."""
    found: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for pattern in _ROLE_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1)
            if not _looks_like_proper_name(name):
                continue
            start = match.start(1)
            end = match.end(1)
            key = (start, end)
            if key in seen:
                continue
            if start > 0 and text[start - 1].isalnum():
                continue
            if end < len(text) and text[end].isalnum():
                continue
            seen.add(key)
            found.append((start, end, name))
    return found
