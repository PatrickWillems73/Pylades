"""Gedeelde outbound-opbouw voor model-adapters.

De "outbound-tekst" is wat het externe LLM zou zien: gedetecteerde spans
worden — ná generalisatie (jaar, PC2, 90+, maand-jaar) — vervangen door
`[CODE]`-placeholders, net als `_substitute_pseudonyms` in
[proxy/pseudonymization.py](proxy/pseudonymization.py). Zowel de
Pylades-baseline-runner als de NER-vergelijkingsrunners delen deze logica zodat
de lek-meting voor elk model op identieke wijze gebeurt.
"""

from __future__ import annotations

from proxy.generalization import GeneralizationConfig, generalize_all
from shared.models import SHORT_TYPE_CODES, Entity


def placeholder(entity: Entity) -> str:
    return f"[{SHORT_TYPE_CODES[entity.entity_type]}]"


def substitute(text: str, entities: list[Entity]) -> str:
    """Vervang niet-overlappende entity-spans door placeholders (langste-first-veilig)."""
    ordered = sorted(entities, key=lambda e: e.start)
    parts: list[str] = []
    last = 0
    prev_end = -1
    for ent in ordered:
        if ent.start < prev_end:
            # Overlap zou tot dubbele vervanging leiden; sla de latere over.
            continue
        parts.append(text[last : ent.start])
        parts.append(placeholder(ent))
        last = ent.end
        prev_end = ent.end
    parts.append(text[last:])
    return "".join(parts)


def build_outbound(
    prompt: str, entities: list[Entity], config: GeneralizationConfig | None = None
) -> str:
    """Generaliseer en maskeer alle entities → tekst zoals het externe LLM die zou zien."""
    gen_text, gen_entities = generalize_all(prompt, entities, config or GeneralizationConfig())
    return substitute(gen_text, gen_entities)
