"""Pure formatting-helpers voor de Audit-pagina.

Splitten we van `ui/pages/4_Audit.py` af zodat het status-label en de
JSON-pretty-printer apart te testen zijn — dat is precies de logica die
in een audit-context fout *mag* zijn, maar nooit zonder dat een test het
roept.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from shared.models import AuditEntry


@dataclass(frozen=True, slots=True)
class StatusBadge:
    """Eén compacte status-aanduiding voor de overzichtstabel.

    `label` is wat de cel laat zien, `tone` is een hint voor de UI om er
    kleur op te plakken (`success` / `warning` / `error` / `neutral`).
    Aparte velden in plaats van één string met markdown-kleur zodat de
    UI vrij is in opmaak (badge, kleur) zonder de helper te raken.
    """

    label: str
    tone: str


def status_badge(entry: AuditEntry) -> StatusBadge:
    """Bepaal het status-label voor één audit-rij.

    Volgorde van checks weerspiegelt prioriteit:
    1. `error` wint altijd — fouten mogen nooit als "ok" verschijnen,
       ook niet als er per ongeluk ook `review_required=True` staat.
    2. `review_required` is het tweede signaal; tussenstaat zonder
       upstream-call, maar geen fout.
    3. Anders: ok.
    """
    if entry.error:
        return StatusBadge(label="error", tone="error")
    if entry.review_required:
        return StatusBadge(label="review", tone="warning")
    return StatusBadge(label="ok", tone="success")


def pretty_json(value: str | None) -> str:
    """Pretty-print JSON-strings; geef bij niet-JSON gewoon de input terug.

    De proxy logt response-bodies als string (de exacte upstream-text of
    een `json.dumps`-resultaat). Voor menselijke leesbaarheid hervatten
    we hier de parsing. Foutieve input gooien zou de audit-pagina laten
    crashen op precies de rijen die het meest interessant zijn (errors,
    upstream-rommel) — onwenselijk.
    """
    if value is None or value == "":
        return ""
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return value
    return json.dumps(parsed, ensure_ascii=False, indent=2)
