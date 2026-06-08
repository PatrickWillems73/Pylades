"""Template-service: CRUD + JSON-deserialisatie van `mode_overrides`.

Eén plek voor `proxy/main.py` (lookup tijdens een request) én voor de
Streamlit Opdrachten-pagina (CRUD vanuit de UI). Houden we deze logica
in twee modules, dan duiken JSON-schema-drift en TWO_WAY-onderbouwings-
validatie op twee verschillende plekken op — en daar wordt iets vergeten.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from shared.db import get_content_connection
from shared.models import EntityType, PseudonymizationMode, Template


def _parse_overrides(raw: str | None) -> dict[EntityType, PseudonymizationMode]:
    """`mode_overrides` staat als JSON in de DB; decode + validate types."""
    if not raw or raw.strip() in ("", "{}"):
        return {}
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"mode_overrides moet JSON-object zijn, kreeg: {type(data).__name__}")
    result: dict[EntityType, PseudonymizationMode] = {}
    for key, value in data.items():
        result[EntityType(key)] = PseudonymizationMode(value)
    return result


def _row_to_template(row: sqlite3.Row) -> Template:
    default_mode_raw = row["default_mode"]
    default_mode = PseudonymizationMode(default_mode_raw) if default_mode_raw is not None else None
    sort_order_raw = row["sort_order"]
    return Template(
        id=int(row["id"]),
        groep=str(row["groep"]),
        naam=str(row["naam"]),
        beschrijving=str(row["beschrijving"] or ""),
        llm_provider=str(row["llm_provider"]),
        llm_naam=str(row["llm_naam"]),
        prompt_tekst=str(row["prompt_tekst"] or ""),
        max_tokens=int(row["max_tokens"]) if row["max_tokens"] is not None else 16_000,
        use_llm=bool(row["use_llm"]) if row["use_llm"] is not None else False,
        default_mode=default_mode,
        mode_overrides=_parse_overrides(row["mode_overrides"]),
        two_way_justification=(
            str(row["two_way_justification"]) if row["two_way_justification"] is not None else None
        ),
        sort_order=int(sort_order_raw) if sort_order_raw is not None else 0,
    )


def get_template(template_id: int) -> Template | None:
    """Returnt de template op id of `None` als hij niet bestaat."""
    with get_content_connection() as conn:
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        return None
    return _row_to_template(row)


def _serialize_overrides(overrides: dict[EntityType, PseudonymizationMode]) -> str:
    return json.dumps(
        {entity_type.value: mode.value for entity_type, mode in overrides.items()},
        sort_keys=True,
    )


def _ordered_template_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM templates ORDER BY sort_order ASC, id ASC"
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _apply_template_order(conn: sqlite3.Connection, ordered_ids: list[int]) -> None:
    for idx, template_id in enumerate(ordered_ids):
        conn.execute(
            "UPDATE templates SET sort_order = ?, updated_at = datetime('now') WHERE id = ?",
            (idx, template_id),
        )


def list_templates() -> list[Template]:
    """Alle opgeslagen templates, in beheerder-volgorde (`sort_order`)."""
    with get_content_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM templates ORDER BY sort_order ASC, id ASC"
        ).fetchall()
    return [_row_to_template(row) for row in rows]


def move_template(template_id: int, delta: int) -> bool:
    """Verplaats een template één positie omhoog (-1) of omlaag (+1) in de lijst."""
    with get_content_connection() as conn:
        order = _ordered_template_ids(conn)
        if template_id not in order:
            return False
        idx = order.index(template_id)
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(order):
            return False
        order[idx], order[new_idx] = order[new_idx], order[idx]
        _apply_template_order(conn, order)
        return True


def upsert_template(template: Template) -> int:
    """Schrijf een template; `id=None` → INSERT, anders UPDATE.

    Returnt de uiteindelijke id. De Pydantic-validator op `Template` heeft
    op dit punt al gegarandeerd dat een TWO_WAY-modus een onderbouving
    heeft (BR-C06); deze functie hoeft dat niet nog eens te checken.
    """
    overrides_json = _serialize_overrides(template.mode_overrides)
    default_mode_value = template.default_mode.value if template.default_mode is not None else None
    with get_content_connection() as conn:
        if template.id is None:
            max_row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM templates"
            ).fetchone()
            next_sort = int(max_row[0]) + 1
            cursor = conn.execute(
                """INSERT INTO templates (
                    groep, naam, beschrijving, llm_provider, llm_naam,
                    prompt_tekst, max_tokens, use_llm, default_mode,
                    mode_overrides, two_way_justification, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    template.groep,
                    template.naam,
                    template.beschrijving,
                    template.llm_provider,
                    template.llm_naam,
                    template.prompt_tekst,
                    template.max_tokens,
                    int(template.use_llm),
                    default_mode_value,
                    overrides_json,
                    template.two_way_justification,
                    next_sort,
                ),
            )
            return int(cursor.lastrowid or 0)
        conn.execute(
            """UPDATE templates SET
                groep = ?, naam = ?, beschrijving = ?,
                llm_provider = ?, llm_naam = ?, prompt_tekst = ?,
                max_tokens = ?, use_llm = ?, default_mode = ?,
                mode_overrides = ?, two_way_justification = ?,
                updated_at = datetime('now')
            WHERE id = ?""",
            (
                template.groep,
                template.naam,
                template.beschrijving,
                template.llm_provider,
                template.llm_naam,
                template.prompt_tekst,
                template.max_tokens,
                int(template.use_llm),
                default_mode_value,
                overrides_json,
                template.two_way_justification,
                template.id,
            ),
        )
        return int(template.id)


def delete_template(template_id: int) -> bool:
    """Verwijder een template; returnt True als er daadwerkelijk geschrapt is.

    FK-cascade is uitgezet; bestaande `audit_log`-rijen met deze template_id
    blijven staan (de auditbelofte van BR-G01 weegt zwaarder dan "schone
    cascade").
    """
    with get_content_connection() as conn:
        cursor = conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        return cursor.rowcount > 0


__all__ = [
    "delete_template",
    "get_template",
    "list_templates",
    "move_template",
    "upsert_template",
]
