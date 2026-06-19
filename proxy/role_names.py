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
# Optioneel extra woorden (max. 3 totaal); sluit sectiekoppen uit (`Opgenomen`, …).
_NON_NAME_WORD = (
    r"Opgenomen|Geboorte(?:datum)?|Leeftijd|Datum|Reden|Behandel(?:ing)?|"
    r"Opname|Ontslag|Conclusie|Beloop|Anamnese|Medicatie|Diagnose|Werk|Consult|"
    r"Gecontroleerd|Opgesteld|Verzonden|Ingesteld|Start(?:te)?"
)
_NAME = rf"({_NAME_WORD}(?:[ \t]+(?!{_NON_NAME_WORD}\b){_NAME_WORD}){{0,2}})"

# Markdown-koppen in synthetische dossiers (`**Arts-assistent:**`).
_MD = r"\*{0,2}"
# Dubbele punt met optionele markdown (`:`, `:**`, `**:`).
_COLON = rf"(?:{_MD}\s*)?:\s*(?:{_MD})?"

# Titel/rol + optionele titel + naam (tot komma, punt of regeleinde).
_LABEL_ROLES = (
    r"verpleegkundig\s+specialist|physician\s+assistant|verwijz(?:er|end)\s+huisarts|"
    r"behandelend(?:\s+(?:internist|specialist))?|hoofdbehandelaar|"
    r"verwijzer(?:\s+\([^)]+\))?|huisarts|"
    r"arts-?assistent|co-?assistent|assisterend\s+arts|zaalarts|supervisor"
)
_LABEL_NAME = re.compile(
    rf"(?i){_MD}(?:{_LABEL_ROLES}){_COLON}\s*"
    rf"(?:(?:dr\.|drs\.|dhr\.|mevrouw|mevr\.)\s+)?"
    rf"{_NAME}"
)

# Dossierkop: `Patiënt: Bakker, voornaam …` / `Patiënt: Mevrouw De Bakker, 74 jaar`.
_DOSSIER_PATIENT = re.compile(
    rf"(?i){_MD}Pati[eë]nt{_COLON}\s*"
    rf"(?:(?:mevrouw|dhr\.)\s+)?"
    rf"({_NAME_WORD})(?:\s*,\s*(?:voornaam|\d))"
)

# `Patiëntnaam: Karim El Idrissi` (tot geboortedatum/regeleinde).
_DOSSIER_PATIENTNAAM = re.compile(
    rf"(?i){_MD}Pati[eë]ntnaam{_COLON}\s*({_NAME})"
)

# `**Notitie verpleegkundige O'Brien:**` (naam vóór dubbele punt).
_NOTITIE_VERPLEEGKUNDIGE = re.compile(
    rf"(?i){_MD}Notitie\s+verpleegkundige\s+({_NAME_WORD}){_COLON}"
)

# Rolwoord direct vóór een naam (zonder dubbele punt). Geen losse `internist` —
# behalve expliciet zonder `dr.` ervoor (internist Castro).
_ROLE_BEFORE_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"consulent(?:\s+\w+)?|fysiotherapeut|apotheker|"
    rf"arts-?assistent|co-?assistent|cardioloog|verpleegkundig\s+specialist|"
    rf"verpleegkundige|radioloog|patholoog|microbioloog|oogarts|zaalarts|supervisor"
    rf")\s+"
    rf"{_NAME}"
)

# `internist Castro` (zonder dr.-titel ertussen).
_INTERNIST_SURNAME = re.compile(
    rf"(?i)\binternist\s+(?!dr\.)({_NAME_WORD})\b"
)

# `Consult diëtetiek door Fernández` / `fysiotherapie ingezet door collega Mukherjee`.
_CONSULT_DOOR = re.compile(
    rf"(?i)\bconsult\s+[^.\n]{{0,50}}?\bdoor\s+({_NAME_WORD})\b"
)
_BY_COLLEGA = re.compile(rf"(?i)\b(?:door|via|bij)\s+collega\s+({_NAME_WORD})\b")

# `Consult longarts Mukherjee aangevraagd` / `Consult cardiologie door Fernández`.
_CONSULT_SPECIALIST = re.compile(
    rf"(?i)\bconsult\s+"
    rf"(?:longarts|cardioloog|internist|oncoloog|nefroloog|neuroloog|pulmonoloog|"
    rf"geneeskundige|specialist)\s+({_NAME_WORD})\b"
)

# `Medeoverleg met longarts Okafor`.
_MEDEOVERLEG = re.compile(
    rf"(?i)\bmedeoverleg\s+met\s+"
    rf"(?:longarts|cardioloog|internist|oncoloog|nefroloog|neuroloog|pulmonoloog)\s+"
    rf"({_NAME_WORD})\b"
)

# `Diëtiste Yamamoto adviseerde` (beroep + werkwoord).
_DIETIST_WORKS = re.compile(
    rf"(?i)\bdi[eë]tist(?:e)?\s+({_NAME_WORD})\s+(?:adviseerde|begeleidde|instrueerde)\b"
)

# `(zie ook geboortenaam Fernández)`.
_GEBOORTENAAM = re.compile(rf"(?i)\bgeboortenaam\s+({_NAME_WORD})\b")

# `naar mantelzorger Bakr` / `Opgesteld door Słowik`.
_NAAR_MANTELZORGER = re.compile(rf"(?i)\bnaar\s+mantelzorger\s+({_NAME_WORD})\b")
_OPGESTELD_DOOR = re.compile(
    rf"(?i)\b(?:opgesteld|medeondertekend|ondertekend)\s+door\s+({_NAME_WORD})\b"
)

# `Verpleegkundige Okonkwo verzorgde` (werkwoord na de naam).
_VERPLEEGKUNDIGE_WERKWOORD = re.compile(
    rf"(?i)\bverpleegkundige\s+({_NAME_WORD})\s+(?:verzorg|begeleid|rapporteer)"
)

# `Verwijzing diëtist Mwangi` (expliciete verwijsregel, niet algemeen diëtist-label).
_REFERRAL_DIETIST = re.compile(
    rf"(?i)\bverwijzing\s+di[eë]tist(?:e)?\s+({_NAME_WORD})"
)

# Familierelatie / contactpersoon (direct na relatie).
_RELATION_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"partner|dochter|zoon|schoonzoon|echtgeno(?:o)?t(?:e)?|moeder|vader|broer|zus|"
    rf"contactpersoon|mantelzorger"
    rf")\s+"
    rf"(?:(?:mevrouw|dhr\.)\s+)?"
    rf"{_NAME}"
)

# Relatie + komma + naam (`dochter, Petrova`; `echtgenote, mevrouw Koningin`).
_RELATION_COMMA_NAME = re.compile(
    rf"(?i)\b(?:"
    rf"partner|dochter|zoon|schoonzoon|echtgeno(?:o)?t(?:e)?|moeder|vader|broer|zus|"
    rf"contactpersoon|mantelzorger"
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
    rf"(?i)\bhuisarts(?![ \t]+(?:dr\.|drs\.))\s+({_NAME_WORD})(?![ \t]+{_NAME_WORD})\b"
)

# Adversariële achternaam die op een beroep lijkt (`huisarts De Dokter`).
_HUISARTS_DE_NAME = re.compile(
    rf"(?i)\b(?:verwezen\s+door\s+)?huisarts(?![ \t]+(?:dr\.|drs\.))\s+{_NAME}"
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
    _DOSSIER_PATIENT,
    _DOSSIER_PATIENTNAAM,
    _NOTITIE_VERPLEEGKUNDIGE,
    _ROLE_BEFORE_NAME,
    _INTERNIST_SURNAME,
    _CONSULT_DOOR,
    _CONSULT_SPECIALIST,
    _MEDEOVERLEG,
    _BY_COLLEGA,
    _DIETIST_WORKS,
    _GEBOORTENAAM,
    _NAAR_MANTELZORGER,
    _OPGESTELD_DOOR,
    _VERPLEEGKUNDIGE_WERKWOORD,
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
    """Extra filter: elke naam-component start met een hoofdletter (Unicode)."""
    parts = surface.split()
    if not parts or len(parts) > 4:
        return False
    if any(re.fullmatch(_NON_NAME_WORD, part, re.IGNORECASE) for part in parts):
        return False
    return all(part and not part[0].islower() for part in parts)


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
