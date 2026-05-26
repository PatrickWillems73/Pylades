"""Manual-review-queue (BR-A04): content-DB-zijde van de pijplijn.

Alleen `get_content_connection` — de queue zit per definitie aan de
*niet-vault*-kant van de scheiding (BR-G02). Schrijft `pending_review`-
entities weg met status PENDING, levert ze terug aan de UI, en biedt de
proxy een resume-pad zodra alles is afgevinkt.

`session_id` wordt idempotent geregistreerd zodat de FK in `review_queue`
en `audit_log` nooit faalt op een nog-niet-bestaande sessie. Dit voorkomt
dat een 423-flow stilletjes crasht omdat de UI de sessie nog niet had
aangemaakt — de proxy is de enige plek waar sessies opdoemen.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Final

from shared.db import get_content_connection
from shared.models import (
    ENTITY_CATEGORY_MAP,
    DetectionLayer,
    Entity,
    EntityCategory,
    EntityType,
    ReviewItem,
    ReviewStatus,
)

_TERMINAL_STATUSES: Final[frozenset[ReviewStatus]] = frozenset(
    {ReviewStatus.ACCEPTED, ReviewStatus.REJECTED, ReviewStatus.MODIFIED}
)


def _ensure_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Maak de sessie aan als die nog niet bestaat (FK-vereiste)."""
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id) VALUES (?)",
        (session_id,),
    )


def _row_to_review_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        original_text=str(row["original_text"]),
        detected_text=str(row["detected_text"]),
        proposed_entity_type=EntityType(row["proposed_entity_type"]),
        proposed_category=EntityCategory(row["proposed_category"]),
        confidence=float(row["confidence"]),
        detection_layer=DetectionLayer(row["detection_layer"]),
        status=ReviewStatus(row["status"]),
        user_decision_entity_type=(
            EntityType(row["user_decision_entity_type"])
            if row["user_decision_entity_type"] is not None
            else None
        ),
        user_decision_at=(
            datetime.fromisoformat(str(row["user_decision_at"]))
            if row["user_decision_at"] is not None
            else None
        ),
        user_decision_note=(
            str(row["user_decision_note"]) if row["user_decision_note"] is not None else None
        ),
        created_at=(
            datetime.fromisoformat(str(row["created_at"]))
            if row["created_at"] is not None
            else None
        ),
    )


def enqueue(session_id: str, original_text: str, entities: list[Entity]) -> list[int]:
    """Schrijf `entities` (uit `DetectionResult.pending_review`) weg als PENDING.

    Returnt de zojuist toegekende `review_queue.id`-waarden in dezelfde
    volgorde als de input — handig voor de UI die deep-links wil tonen.
    `original_text` is de volledige prompt; de UI knipt daar zelf z'n
    context-snippet uit zodat we hier niet op een woord-grens hoeven raden.
    """
    if not entities:
        return []

    ids: list[int] = []
    with get_content_connection() as conn:
        _ensure_session(conn, session_id)
        for ent in entities:
            category = (
                ent.category if ent.category is not None else ENTITY_CATEGORY_MAP[ent.entity_type]
            )
            cursor = conn.execute(
                """INSERT INTO review_queue (
                    session_id, original_text, detected_text,
                    proposed_entity_type, proposed_category,
                    confidence, detection_layer, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    original_text,
                    ent.original,
                    ent.entity_type.value,
                    category.value,
                    ent.confidence,
                    ent.detection_layer.value,
                    ReviewStatus.PENDING.value,
                ),
            )
            ids.append(int(cursor.lastrowid or 0))
    return ids


def get_pending(session_id: str) -> list[ReviewItem]:
    """Alle items voor `session_id` met status PENDING, oudste eerst."""
    with get_content_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM review_queue
               WHERE session_id = ? AND status = ?
               ORDER BY id ASC""",
            (session_id, ReviewStatus.PENDING.value),
        ).fetchall()
    return [_row_to_review_item(row) for row in rows]


def get_item(item_id: int) -> ReviewItem | None:
    """Eén item op id; `None` als de rij niet bestaat."""
    with get_content_connection() as conn:
        row = conn.execute("SELECT * FROM review_queue WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    return _row_to_review_item(row)


def decide(
    item_id: int,
    status: ReviewStatus,
    modified_type: EntityType | None = None,
    note: str | None = None,
) -> ReviewItem:
    """Verander de status van een item; returnt de bijgewerkte rij.

    - `ReviewStatus.ACCEPTED`: type ongewijzigd; `modified_type` wordt
      genegeerd. Originele detectie geldt.
    - `ReviewStatus.MODIFIED`: `modified_type` is verplicht en wordt
      opgeslagen in `user_decision_entity_type`.
    - `ReviewStatus.REJECTED`: item wordt niet meegenomen bij resume;
      `modified_type` wordt genegeerd.
    - `ReviewStatus.PENDING`: niet toegestaan via `decide` (rollback zou
      tot stille verlies van auditbare beslissing leiden).
    """
    if status is ReviewStatus.PENDING:
        raise ValueError("decide(): kan een item niet terug naar PENDING zetten")
    if status is ReviewStatus.MODIFIED and modified_type is None:
        raise ValueError("decide(MODIFIED): modified_type is verplicht")
    if status is not ReviewStatus.MODIFIED and modified_type is not None:
        modified_type = None

    now_iso = datetime.now(UTC).isoformat(timespec="seconds")
    stored_type = modified_type.value if modified_type is not None else None

    with get_content_connection() as conn:
        cursor = conn.execute(
            """UPDATE review_queue
                  SET status = ?,
                      user_decision_entity_type = ?,
                      user_decision_at = ?,
                      user_decision_note = ?
                WHERE id = ?""",
            (status.value, stored_type, now_iso, note, item_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"review_queue.id={item_id} bestaat niet")

    item = get_item(item_id)
    if item is None:
        raise RuntimeError(f"review_queue.id={item_id} verdween na UPDATE (race?); inspecteer DB")
    return item


def list_sessions_with_pending() -> list[tuple[str, int]]:
    """Lever sessies met openstaande review-items, oudste eerst.

    Returnt `(session_id, pending_count)`-paren — de UI gebruikt het
    aantal als badge naast het sessie-label. We sorteren op het laagste
    auto-increment-id (= oudste insertie) zodat een sessie die al langer
    wacht bovenin staat. `created_at` lijkt logischer maar heeft slechts
    seconde-resolutie in SQLite en levert ties op bij snelle inserts;
    `id` is monotoon en deterministisch.
    """
    with get_content_connection() as conn:
        rows = conn.execute(
            """SELECT session_id,
                      COUNT(*) AS pending_count,
                      MIN(id) AS first_id
                 FROM review_queue
                WHERE status = ?
             GROUP BY session_id
             ORDER BY first_id ASC""",
            (ReviewStatus.PENDING.value,),
        ).fetchall()
    return [(str(r["session_id"]), int(r["pending_count"])) for r in rows]


def all_resolved(session_id: str) -> bool:
    """True iff er geen PENDING-items meer zijn voor deze sessie.

    Een sessie zonder review-items telt ook als 'resolved' — `enqueue([])`
    is een geldige no-op en mag de proxy niet blokkeren.
    """
    with get_content_connection() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n FROM review_queue
               WHERE session_id = ? AND status = ?""",
            (session_id, ReviewStatus.PENDING.value),
        ).fetchone()
    return int(row["n"]) == 0


def get_accepted_entities(session_id: str) -> list[Entity]:
    """Lever resolved items terug als `Entity` voor de resume-flow.

    Rejected items vallen er bewust uit; modified items komen mee met het
    door de operator gekozen type. Spans (`start`/`end`) zetten we op 0:0
    omdat ze niet zijn opgeslagen — re-detection in de resume-call vult
    spans opnieuw in en de operator-beslissing dient als override.
    """
    with get_content_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM review_queue
               WHERE session_id = ? AND status IN (?, ?)
               ORDER BY id ASC""",
            (
                session_id,
                ReviewStatus.ACCEPTED.value,
                ReviewStatus.MODIFIED.value,
            ),
        ).fetchall()

    out: list[Entity] = []
    for row in rows:
        status = ReviewStatus(row["status"])
        if status is ReviewStatus.MODIFIED:
            entity_type = EntityType(row["user_decision_entity_type"])
        else:
            entity_type = EntityType(row["proposed_entity_type"])
        out.append(
            Entity(
                original=str(row["detected_text"]),
                entity_type=entity_type,
                confidence=float(row["confidence"]),
                detection_layer=DetectionLayer(row["detection_layer"]),
                start=0,
                end=len(str(row["detected_text"])),
            )
        )
    return out


def _is_terminal(status: ReviewStatus) -> bool:
    return status in _TERMINAL_STATUSES
