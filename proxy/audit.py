"""Append-only audit-log (BR-G01) in `pylades-content.db`.

Logt elke proxy-call met **beide** versies van prompt en response, zodat
audit en debugging mogelijk blijven zonder ooit de vault aan te raken
(BR-G02). Importeert daarom uitsluitend `get_content_connection`; de
AST-waakhond in `tests/test_db_separation.py` faalt bij elke poging tot
cross-import naar `get_vault_connection`.

`session_id` wordt idempotent in `sessions` aangemaakt zodat de FK in
`audit_log` nooit faalt op een sessie die door de proxy-flow zelf in
diezelfde request voor het eerst opduikt.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from shared.db import get_content_connection
from shared.models import AuditEntry


def _ensure_session(conn: sqlite3.Connection, session_id: str) -> None:
    """FK-vereiste: sessie bestaat in `sessions` voor de audit-insert."""
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id) VALUES (?)",
        (session_id,),
    )


def _row_to_audit_entry(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        template_id=(int(row["template_id"]) if row["template_id"] is not None else None),
        original_prompt=str(row["original_prompt"]),
        pseudonymized_prompt=str(row["pseudonymized_prompt"]),
        response_pseudonymized=(
            str(row["response_pseudonymized"])
            if row["response_pseudonymized"] is not None
            else None
        ),
        response_depseudonymized=(
            str(row["response_depseudonymized"])
            if row["response_depseudonymized"] is not None
            else None
        ),
        llm_provider=(str(row["llm_provider"]) if row["llm_provider"] is not None else None),
        llm_model=(str(row["llm_model"]) if row["llm_model"] is not None else None),
        avg_confidence=(
            float(row["avg_confidence"]) if row["avg_confidence"] is not None else None
        ),
        review_required=bool(int(row["review_required"])),
        error=str(row["error"]) if row["error"] is not None else None,
        created_at=(
            datetime.fromisoformat(str(row["created_at"]))
            if row["created_at"] is not None
            else None
        ),
    )


def log_request(
    *,
    session_id: str,
    original_prompt: str,
    pseudonymized_prompt: str,
    template_id: int | None = None,
    response_pseudonymized: str | None = None,
    response_depseudonymized: str | None = None,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    avg_confidence: float | None = None,
    review_required: bool = False,
    error: str | None = None,
) -> int:
    """Schrijf één rij naar `audit_log`; returnt de toegekende `id`.

    Keyword-only om dezelfde reden als bij `pseudonymize`: het argument-
    aantal is groot (11), elke verkeerde positionele volgorde zou stilletjes
    de originele en pseudoniem-prompt verwisselen — een privacy-incident
    dat geen typesysteem kan vangen.
    """
    with get_content_connection() as conn:
        _ensure_session(conn, session_id)
        cursor = conn.execute(
            """INSERT INTO audit_log (
                session_id, template_id, original_prompt, pseudonymized_prompt,
                response_pseudonymized, response_depseudonymized,
                llm_provider, llm_model, avg_confidence, review_required, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                template_id,
                original_prompt,
                pseudonymized_prompt,
                response_pseudonymized,
                response_depseudonymized,
                llm_provider,
                llm_model,
                avg_confidence,
                1 if review_required else 0,
                error,
            ),
        )
    return int(cursor.lastrowid or 0)


def get_recent_logs(limit: int = 50) -> list[AuditEntry]:
    """Recente audit-entries (default 50), nieuwste eerst.

    `limit` wordt geclampt op [1, 1000]: minder dan 1 is zinloos, meer dan
    1000 begint een UI-pagina te verlammen — paginering komt in v1.0.
    """
    bounded = max(1, min(int(limit), 1000))
    with get_content_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    return [_row_to_audit_entry(row) for row in rows]


def get_log_by_id(entry_id: int) -> AuditEntry | None:
    """Eén entry op id; `None` als hij niet bestaat."""
    with get_content_connection() as conn:
        row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    return _row_to_audit_entry(row)


def get_logs_by_session(session_id: str) -> list[AuditEntry]:
    """Alle entries voor één sessie, oudste eerst (chronologisch lezen)."""
    with get_content_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
    return [_row_to_audit_entry(row) for row in rows]
