"""Uitbreiding van NAME-spans met NL-tussenvoegsels en aanspreekvormen.

NER-backends (spaCy, DEDUCE) pakken vaak alleen het achternaamdeel (`Smid`,
`Jansen`) terwijl de gold-span het volledige surface-form bevat (`De Smid`,
`de heer Jansen`). Voor de leak-KPI (<100% dekking = lek) breiden we de span
naar links uit over bekende prefixen, mits die direct voor de detectie staan
en niet midden in een woord beginnen.
"""

from __future__ import annotations

import re

# Langste eerst: `de heer` vóór losse `de`.
_NAME_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:de\s+heer|mevrouw|meneer|mevr\.|dhr\.|mr\.|dr\.|drs\.)\s+$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:(?:van\s+(?:de|den|der|'t)|van|de|den|der|te|ten|ter|von|'t)\s+)+$",
        re.IGNORECASE,
    ),
)


def expand_name_span_left(text: str, start: int, end: int) -> tuple[int, int]:
    """Breid een NAME-span uit naar links over tussenvoegsel/aanspreekvorm."""
    if start <= 0:
        return start, end
    left = text[:start]
    for pattern in _NAME_PREFIX_PATTERNS:
        match = pattern.search(left)
        if match is None:
            continue
        new_start = match.start()
        if new_start > 0 and (text[new_start - 1].isalnum() or text[new_start - 1] == "'"):
            continue
        return new_start, end
    return start, end


_APOSTROPHE_NAME_SUFFIX = re.compile(r"[''][\w-]+")


def expand_name_span_right(text: str, start: int, end: int) -> tuple[int, int]:
    """Breid een NAME-span uit naar rechts over apostrof-achtervoegsels (`N'Diaye`)."""
    while end < len(text):
        match = _APOSTROPHE_NAME_SUFFIX.match(text, end)
        if match is None:
            break
        end = match.end()
    return start, end


def expand_name_span(text: str, start: int, end: int) -> tuple[int, int]:
    """Breid een NAME-span links én rechts uit waar dat surface-form compleet maakt."""
    start, end = expand_name_span_left(text, start, end)
    start, end = expand_name_span_right(text, start, end)
    return start, end
