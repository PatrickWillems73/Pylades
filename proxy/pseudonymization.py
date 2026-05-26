"""HMAC-pseudonimisering en tweeweg-terugvertaling (BR-C01, BR-C06).

Leest de super-default modus uit de content-`config`-tabel en resolved per
entity de effectieve modus via template-default + overrides. `mapping.py`
bevat de vault-I/O; dit bestand orkestreert tekstvervanging en moduslogica.
"""

from __future__ import annotations

from proxy.mapping import PseudonymManager, replace_two_way_pseudonyms
from shared.config import settings
from shared.crypto import derive_session_key, load_or_create_secret, make_pseudonym
from shared.db import get_config_value, get_vault_connection
from shared.models import Entity, EntityType, PseudonymizationMode, Template


def get_super_default_pseudonymization_mode() -> PseudonymizationMode:
    """Super-default uit `config` (BR-C06); default is ONE_WAY."""
    raw = get_config_value(
        "super_default_pseudonymization_mode",
        PseudonymizationMode.ONE_WAY.value,
    )
    if raw is None or raw.strip() == "":
        return PseudonymizationMode.ONE_WAY
    try:
        return PseudonymizationMode(raw.strip().lower())
    except ValueError:
        return PseudonymizationMode.ONE_WAY


def resolve_effective_mode(
    template: Template,
    entity_type: EntityType,
    super_default: PseudonymizationMode,
) -> PseudonymizationMode:
    """Drie-laagse resolver: override → template-default → super-default."""
    mode, _source = resolve_effective_mode_with_source(template, entity_type, super_default)
    return mode


def resolve_effective_mode_with_source(
    template: Template,
    entity_type: EntityType,
    super_default: PseudonymizationMode,
) -> tuple[PseudonymizationMode, str]:
    """Zelfde resolver maar levert ook de bron-annotatie voor de UI.

    Bron-annotaties komen letterlijk uit de prompt-spec (BR-C06):
    `override`, `template-default`, `super-default`. De UI plakt deze op
    de modus om het transparantie-principe waar te maken.
    """
    override = template.mode_overrides.get(entity_type)
    if override is not None:
        return override, "override"
    if template.default_mode is not None:
        return template.default_mode, "template-default"
    return super_default, "super-default"


def _substitute_pseudonyms(text: str, entities: list[Entity], pseudonyms: list[str]) -> str:
    segments = sorted(
        zip(
            [e.start for e in entities],
            [e.end for e in entities],
            pseudonyms,
            strict=True,
        ),
        key=lambda t: t[0],
    )
    prev_end = -1
    for start, end, _pseudo in segments:
        if start < prev_end:
            raise ValueError("Overlappende entities bij pseudonimisering")
        prev_end = end
    parts: list[str] = []
    last = 0
    for start, end, pseudo in segments:
        parts.append(text[last:start])
        parts.append(pseudo)
        last = end
    parts.append(text[last:])
    return "".join(parts)


def pseudonymize(
    text: str,
    entities: list[Entity],
    session_id: str,
    template: Template,
    *,
    manager: PseudonymManager | None = None,
) -> tuple[str, list[Entity]]:
    """Vervang entities door pseudoniemen en persist mappings in de vault.

    Retourneert `(pseudonymized_text, entities_met_pseudonym_en_effective_mode)`.
    """
    mgr = manager if manager is not None else PseudonymManager.from_session(session_id)
    super_default = get_super_default_pseudonymization_mode()

    updated: list[Entity] = []
    pseudos: list[str] = []

    for ent in entities:
        mode = resolve_effective_mode(template, ent.entity_type, super_default)
        ent_mode = ent.model_copy(update={"effective_mode": mode})
        pseudo = mgr.add_entity(ent_mode, mode)
        pseudos.append(pseudo)
        updated.append(ent_mode.model_copy(update={"pseudonym": pseudo}))

    mgr.persist()
    new_text = _substitute_pseudonyms(text, entities, pseudos)
    return new_text, updated


def pseudonymize_dry_run(
    text: str,
    entities: list[Entity],
    session_id: str,
    template: Template,
) -> tuple[str, list[Entity]]:
    """Pseudonimiseer in-memory zonder vault-writes (Testruns-pagina preview).

    Hetzelfde HMAC-pseudoniem als `pseudonymize()` (deterministisch op
    session_key + entity), maar slaat niks op. Geschikt voor "wat zou
    er gebeuren?"-previews waarbij we de vault niet willen vervuilen
    met weggegooide mappings. De gebruiker drukt daarna pas op
    "Verstuur" — die call genereert een verse sessie en doet de echte
    pijplijn inclusief persist.
    """
    super_default = get_super_default_pseudonymization_mode()
    secret = load_or_create_secret(settings.global_secret_path)
    session_key = derive_session_key(secret, session_id)

    updated: list[Entity] = []
    pseudos: list[str] = []
    for ent in entities:
        mode = resolve_effective_mode(template, ent.entity_type, super_default)
        pseudo = make_pseudonym(session_key, ent.original, ent.entity_type)
        pseudos.append(pseudo)
        updated.append(
            ent.model_copy(update={"pseudonym": pseudo, "effective_mode": mode}),
        )

    new_text = _substitute_pseudonyms(text, entities, pseudos)
    return new_text, updated


def depseudonymize(text: str, session_id: str) -> str:
    """Vervang pseudoniemen waarvan de mapping TWO_WAY is (BR-C06)."""
    with get_vault_connection() as conn:
        rows = conn.execute(
            """SELECT pseudonym, original FROM mappings
               WHERE session_id = ? AND pseudonymization_mode = ?""",
            (session_id, PseudonymizationMode.TWO_WAY.value),
        ).fetchall()
    pairs = [(str(r["pseudonym"]), str(r["original"])) for r in rows]
    return replace_two_way_pseudonyms(text, pairs)
