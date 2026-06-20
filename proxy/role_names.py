"""Context-gebaseerde NAME-detectie voor zorgrollen en relatie-aanduidingen.

DEDUCE mist in adversariële dossiers vaak achternamen die expliciet achter een
rol staan (`Verpleegkundig specialist: Okonkwo`, `partner Olufemi Adeyemi`,
`dochter, Petrova`). Deze heuristiek vult NAME-spans aan; regex blijft leidend
bij overlap in de pijplijn-runner. Bedoeld als lichtgewicht alternatief voor
een GLiNER NAME-fallback in eval/productie.
"""

from __future__ import annotations

import re

# Eén naamwoord: hoofdletter-woord, initialen (`J.`, `L.`) of tussenvoegsel.
_TUSSENVOEGSEL = r"(?:[Dd]e|[Vv]an|[Dd]er|[Dd]en|[Tt]er|[Ee]l|[Ii]bn|'t)"
_NAME_WORD = r"(?-i:(?:[A-Z]\.|[^\W\d_a-z][\w'-]*))"
# Optioneel extra woorden (max. 3 totaal); sluit sectiekoppen uit (`Opgenomen`, …).
_NON_NAME_WORD = (
    r"Opgenomen|Geboorte(?:datum)?|Leeftijd|Datum|Reden|Behandel(?:ing)?|"
    r"Opname|Ontslag|Conclusie|Beloop|Anamnese|Medicatie|Diagnose|Werk|Consult|"
    r"Gecontroleerd|Opgesteld|Verzonden|Ingesteld|Start(?:te)?"
)
_NAME = (
    rf"({_NAME_WORD}(?:[ \t]+(?!{_NON_NAME_WORD}\b)"
    rf"(?:{_TUSSENVOEGSEL}\s+)?{_NAME_WORD}){{0,2}})"
)

# Markdown-koppen in synthetische dossiers (`**Arts-assistent:**`).
_MD = r"\*{0,2}"
# Dubbele punt met optionele markdown (`:`, `:**`, `**:`).
_COLON = rf"(?:{_MD}\s*)?:\s*(?:{_MD})?"

# Titel/rol + optionele titel + naam (tot komma, punt of regeleinde).
_LABEL_ROLES = (
    r"verpleegkundig\s+specialist|physician\s+assistant|verwijz(?:er|end)\s+huisarts|"
    r"behandelend(?:\s+(?:internist|specialist))?|hoofdbehandelaar|"
    r"verwijzer(?:\s+\([^)]+\))?|huisarts|"
    r"arts-?assistent|co-?assistent|assisterend\s+arts|zaalarts|supervisor|"
    r"di[eë]tist(?:e)?|co-?assistent\s+aanwezig"
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

# `Patiënt: Bakker, voornaam Wilhelmina` (losse voornaam in dossierkop).
_DOSSIER_VOORNAAM = re.compile(
    rf"(?i){_MD}Pati[eë]nt{_COLON}\s*{_NAME_WORD}\s*,\s*voornaam\s+({_NAME})\b"
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
    rf"consulent(?:\s+\w+)?|apotheker|triagist|"
    rf"arts-?assistent|co-?assistent|cardioloog|verpleegkundig\s+specialist|"
    rf"verpleegkundige|radioloog|patholoog|microbioloog|oogarts|zaalarts|supervisor|"
    rf"di[eë]tist(?:e)?"
    rf")\s+"
    rf"{_NAME}"
)

# `fysiotherapeut Timmerman van …` / `wijkverpleegkundige Mol van …` (alleen achternaam).
_ROLE_BEFORE_NAME_SINGLE = re.compile(
    rf"(?i)\b(?:fysiotherapeut|wijkverpleegkundige)\s+({_NAME_WORD})\b"
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

# `Verwijzer: huisarts J. de Groot, Huisartsenpraktijk …`.
_VERWIJzer_HUISARTS = re.compile(
    rf"(?i)\bverwijzer{_COLON}\s*(huisarts\s+[^,\n]+)"
)

# `dr. Al-Rashidi (radiotherapeut)` / `Dr. Al-Rashidi adviseert`.
_DR_ROLE_PAREN = re.compile(
    rf"(?i)\bdr\.?\s+({_NAME_WORD})\s+\([^)]{{3,50}}\)"
)
_DR_ADVISEERT = re.compile(
    rf"(?i)\bDr\.\s+({_NAME_WORD})\s+(?:adviseert|besproken|rapporteert)\b"
)

# `- Tilanus, patholoog` / `- De Wit, verpleegkundig specialist`.
_DASH_NAME_COMMA_ROLE = re.compile(
    rf"(?i)(?:^|\n)\s*-\s+(?:drs?\.?\s+)?({_NAME})\s*,\s*"
    rf"(?:patholoog|radioloog|cardioloog|internist|chirurg|oncolog\w+|"
    rf"verpleegkundig\s+specialist|medisch\s+oncoloog|radiotherapeut)\b"
)
# `- verpleegkundig specialist L. Smit`.
_DASH_ROLE_LABEL = re.compile(
    rf"(?i)(?:^|\n)\s*-\s+(verpleegkundig\s+specialist\s+{_NAME})"
)
# `patholoog (Tilanus):`.
_ROLE_PAREN_NAME = re.compile(
    rf"(?i)\b(?:patholoog|radioloog|cardioloog|internist|chirurg)\s+\(({_NAME_WORD})\):"
)
# `Verslag opgesteld door verpleegkundig specialist L. Smit`.
_OPGESTELD_DOOR_ROLE = re.compile(
    rf"(?i)\b(?:verslag\s+)?opgesteld\s+door\s+(verpleegkundig\s+specialist\s+{_NAME})"
)

# `naar mantelzorger Bakr` / `Opgesteld door Słowik` / `cc naar Oyelaran`.
_NAAR_MANTELZORGER = re.compile(rf"(?i)\bnaar\s+mantelzorger\s+({_NAME_WORD})\b")
_CC_NAAR = re.compile(rf"(?i)\bcc\s+naar\s+({_NAME_WORD})\b")
_OPGESTELD_DOOR = re.compile(
    rf"(?i)\b(?:opgesteld|medeondertekend|ondertekend)\s+door\s+({_NAME_WORD})\b"
)

# `Controle … op de polikliniek bij Mukherjee`.
_POLIKLINIEK_BIJ = re.compile(
    rf"(?i)\b(?:op\s+de\s+)?polikliniek\s+bij\s+({_NAME_WORD})\b"
)

# `Kopie aan: Huisarts dr. M. de Jong` / `Verstuurd aan huisarts: huisartsenpraktijk De Esdoorn`.
_KOPIE_AAN_HUISARTS = re.compile(
    rf"(?i)\bkopie\s+aan{_COLON}\s*(?:huisarts\s+)?((?:dr\.|drs\.)\s+{_NAME})"
)
_VERSTUURD_HUISARTS = re.compile(
    rf"(?i)\bverstuurd\s+aan\s+huisarts{_COLON}\s*(huisartsenpraktijk\s+{_NAME})"
)

# `naar fysiotherapeut Timmerman` / `fysiotherapie bij Schipper`.
_NAAR_FYSIOTHERAPEUT = re.compile(
    rf"(?i)\bnaar\s+fysiotherapeut\s+({_NAME_WORD})\b"
)
_FYSIOTHERAPIE_BIJ = re.compile(rf"(?i)\bfysiotherapie\s+bij\s+({_NAME_WORD})\b")

# `Fysiotherapie ingeschakeld via Mwangi` / `verstrekt door Fernández`.
_INGESCHAKELD_VIA = re.compile(rf"(?i)\bingeschakeld\s+via\s+({_NAME_WORD})\b")
_VERSTREKT_DOOR = re.compile(rf"(?i)\bverstrekt\s+door\s+({_NAME_WORD})\b")

# `Contactpersoon: schoonzoon Okonkwo`.
_CONTACTPERSOON_RELATIE = re.compile(
    rf"(?i)\bcontactpersoon{_COLON}\s*"
    rf"(?:partner|dochter|zoon|schoonzoon|echtgeno(?:o)?t(?:e)?|moeder|vader|broer|zus|"
    rf"mantelzorger)\s+"
    rf"(?:(?:mevrouw|dhr\.)\s+)?"
    rf"{_NAME}"
)

# `oncoloog dr. Al-Rashid` (volledige hyphenated achternaam).
_SPECIALIST_DR = re.compile(
    rf"(?i)\b(?:oncoloog|cardioloog|internist|chirurg|longarts|nefroloog|neuroloog|"
    rf"pulmonoloog|radiotherapeut)\s+(?:dr\.|drs\.)\s+({_NAME_WORD})\b"
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

# `eerdergenoemde Okonkwo` (terugverwijzing in lopende tekst).
_EERDERGENOEMDE = re.compile(rf"(?i)\beerdergenoemde\s+({_NAME_WORD})\b")

# `Fysiotherapie door Mwangi` / `Fysiotherapie ingezet door Haddad`.
_THROUGH_NAME = re.compile(
    rf"(?i)\b(?:fysiotherapie|physiotherapie|ergotherapie|logopedie)\s+"
    rf"(?:ingezet\s+)?door\s+({_NAME_WORD})\b"
)

# `naar huisarts Okonkwo en` (zonder dubbele punt). Sla tussenvoegsel over
# (`huisarts De Dokter` → `_HUISARTS_DE_NAME`).
_HUISARTS_SURNAME = re.compile(
    rf"(?i)\bhuisarts(?![ \t]+(?:dr\.|drs\.))\s+({_NAME})\b"
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
    _DOSSIER_VOORNAAM,
    _DOSSIER_PATIENTNAAM,
    _NOTITIE_VERPLEEGKUNDIGE,
    _ROLE_BEFORE_NAME,
    _ROLE_BEFORE_NAME_SINGLE,
    _INTERNIST_SURNAME,
    _CONSULT_DOOR,
    _CONSULT_SPECIALIST,
    _MEDEOVERLEG,
    _VERWIJzer_HUISARTS,
    _DR_ROLE_PAREN,
    _DR_ADVISEERT,
    _DASH_NAME_COMMA_ROLE,
    _DASH_ROLE_LABEL,
    _ROLE_PAREN_NAME,
    _OPGESTELD_DOOR_ROLE,
    _BY_COLLEGA,
    _DIETIST_WORKS,
    _GEBOORTENAAM,
    _NAAR_MANTELZORGER,
    _CC_NAAR,
    _POLIKLINIEK_BIJ,
    _KOPIE_AAN_HUISARTS,
    _VERSTUURD_HUISARTS,
    _NAAR_FYSIOTHERAPEUT,
    _FYSIOTHERAPIE_BIJ,
    _INGESCHAKELD_VIA,
    _VERSTREKT_DOOR,
    _CONTACTPERSOON_RELATIE,
    _EERDERGENOEMDE,
    _SPECIALIST_DR,
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


_TUSSENVOEGSEL_PART = re.compile(
    r"^(?:de|van|der|den|ter|el|ibn|'t)$",
    re.IGNORECASE,
)
_INITIAL_PART = re.compile(r"^[A-Z]\.$")


def _looks_like_proper_name(surface: str) -> bool:
    """Extra filter: elke naam-component start met een hoofdletter (Unicode)."""
    surface = re.sub(r"(?i)^huisarts\s+", "", surface.strip())
    surface = re.sub(r"(?i)^huisartsenpraktijk\s+", "", surface.strip())
    surface = re.sub(r"(?i)^verpleegkundig\s+specialist\s+", "", surface.strip())
    surface = re.sub(r"(?i)^(?:dr\.|drs\.)\s+", "", surface.strip())
    parts = surface.split()
    if not parts or len(parts) > 4:
        return False
    for part in parts:
        if _INITIAL_PART.match(part) or _TUSSENVOEGSEL_PART.match(part):
            continue
        if re.fullmatch(_NON_NAME_WORD, part, re.IGNORECASE):
            return False
        if not part or part[0].islower():
            return False
    return True


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
    return _drop_subsumed_spans(found)


def _drop_subsumed_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Verwijder spans die volledig in een andere span vallen."""
    if len(spans) <= 1:
        return spans
    kept: list[tuple[int, int, str]] = []
    for i, (s1, e1, name1) in enumerate(spans):
        subsumed = False
        for j, (s2, e2, _) in enumerate(spans):
            if i == j:
                continue
            if s2 <= s1 and e2 >= e1 and (s2, e2) != (s1, e1):
                subsumed = True
                break
        if not subsumed:
            kept.append((s1, e1, name1))
    return kept
