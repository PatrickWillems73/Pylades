"""Pseudoniem-state en vault-persistatie (BR-G02, BR-C01).

Alleen `get_vault_connection` — geen content-db: `proxy/audit.py` mag de
vault niet, `mapping.py` mag de content-db niet (AST-waakhond in
`tests/test_db_separation.py`).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Final

from shared.config import settings
from shared.crypto import derive_session_key, load_or_create_secret, make_pseudonym
from shared.db import get_vault_connection
from shared.models import (
    ENTITY_CATEGORY_MAP,
    DetectionLayer,
    Entity,
    EntityCategory,
    EntityType,
    PseudonymizationMode,
)

_CSV_COLUMNS: Final[tuple[str, ...]] = (
    "session_id",
    "pseudonym",
    "original",
    "entity_type",
    "entity_category",
    "pseudonymization_mode",
    "created_at",
)

_PENDING_KEYS: Final[tuple[str, ...]] = (
    "session_id",
    "pseudonym",
    "original",
    "entity_type",
    "entity_category",
    "pseudonymization_mode",
    "confidence",
    "detection_layer",
    "generalized_to",
)


@dataclass(slots=True)
class _InsertPayload:
    session_id: str
    pseudonym: str
    original: str
    entity_type: str
    entity_category: str
    pseudonymization_mode: str
    confidence: float
    detection_layer: str
    generalized_to: str | None


class PseudonymManager:
    """Beheer pseudoniemen binnen één sessie.

    - Generatie via HMAC (`shared.crypto`, BR-C01)
    - Schrijft naar `pylades-vault.db` (BR-G02)
    - Houdt een geheugen-cache voor dubbele `original`+`entity_type` binnen
      dezelfde run vóór `persist()` en daarna via SELECT
    """

    def __init__(self, session_id: str, session_key: bytes) -> None:
        self._session_id = session_id
        self._session_key = session_key
        self._cache: dict[tuple[str, str], str] = {}
        self._pending: list[_InsertPayload] = []

    @classmethod
    def from_session(cls, session_id: str) -> PseudonymManager:
        """Herleid dezelfde session_key als bij eerdere calls met dit id."""
        secret = load_or_create_secret(settings.global_secret_path)
        key = derive_session_key(secret, session_id)
        return cls(session_id, key)

    def add_entity(
        self,
        entity: Entity,
        effective_mode: PseudonymizationMode,
    ) -> str:
        """Reserveer of hergebruik een pseudoniem; queue een vault-insert (BR-C06)."""
        cache_key = (entity.original, entity.entity_type.value)
        if cache_key in self._cache:
            return self._cache[cache_key]

        existing = self._fetch_existing_pseudonym(cache_key[0], cache_key[1])
        if existing is not None:
            self._cache[cache_key] = existing
            return existing

        pseudonym = make_pseudonym(self._session_key, entity.original, entity.entity_type)
        category = (
            entity.category
            if entity.category is not None
            else ENTITY_CATEGORY_MAP[entity.entity_type]
        )
        self._pending.append(
            _InsertPayload(
                session_id=self._session_id,
                pseudonym=pseudonym,
                original=entity.original,
                entity_type=entity.entity_type.value,
                entity_category=category.value,
                pseudonymization_mode=effective_mode.value,
                confidence=entity.confidence,
                detection_layer=entity.detection_layer.value,
                generalized_to=entity.generalized_to,
            )
        )
        self._cache[cache_key] = pseudonym
        return pseudonym

    def _fetch_existing_pseudonym(self, original: str, entity_type: str) -> str | None:
        with get_vault_connection() as conn:
            row = conn.execute(
                "SELECT pseudonym FROM mappings "
                "WHERE session_id = ? AND original = ? AND entity_type = ?",
                (self._session_id, original, entity_type),
            ).fetchone()
        if row is None:
            return None
        return str(row["pseudonym"])

    def persist(self) -> None:
        """Flush alle pending inserts naar de vault (een transactie)."""
        if not self._pending:
            return
        placeholders = ", ".join("?" for _ in _PENDING_KEYS)
        columns = ", ".join(_PENDING_KEYS)
        sql = f"INSERT INTO mappings ({columns}) VALUES ({placeholders})"
        with get_vault_connection() as conn:
            for payload in self._pending:
                conn.execute(
                    sql,
                    tuple(getattr(payload, k) for k in _PENDING_KEYS),
                )
        self._pending.clear()

    def deanonymize(self, text: str) -> str:
        """Vervang enkel TWO_WAY-pseudoniemen door hun opgeslagen `original`.

        Langste pseudoniemen eerst om nested/prefix-conflicten te vermijden.
        """
        with get_vault_connection() as conn:
            rows = conn.execute(
                """SELECT pseudonym, original FROM mappings
                   WHERE session_id = ? AND pseudonymization_mode = ?""",
                (self._session_id, PseudonymizationMode.TWO_WAY.value),
            ).fetchall()
        pairs = [(str(r["pseudonym"]), str(r["original"])) for r in rows]
        return replace_two_way_pseudonyms(text, pairs)


def export_mappings_csv() -> str:
    """Lever alle vault-mappings als CSV-string (Config-pagina, sleutelrotatie).

    Kolommen komen 1-op-1 uit de prompt-spec en zijn stabiel: caller kan
    de string in een `download_button` plakken zonder verdere formatting.
    `original` is plaintext; de gebruiker is zelf verantwoordelijk voor
    veilige opslag (README-vermelding bij sleutelrotatie).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_CSV_COLUMNS)
    with get_vault_connection() as conn:
        rows = conn.execute(
            "SELECT " + ", ".join(_CSV_COLUMNS) + " FROM mappings ORDER BY id ASC"
        ).fetchall()
    for row in rows:
        writer.writerow([row[col] for col in _CSV_COLUMNS])
    return buffer.getvalue()


def list_entities_for_session(session_id: str) -> list[Entity]:
    """Lees opgeslagen mappings voor één sessie als `Entity`-lijst (UI/audit-sync)."""
    with get_vault_connection() as conn:
        rows = conn.execute(
            """SELECT original, pseudonym, entity_type, entity_category,
                      pseudonymization_mode, confidence, detection_layer
               FROM mappings WHERE session_id = ? ORDER BY id ASC""",
            (session_id,),
        ).fetchall()
    out: list[Entity] = []
    for row in rows:
        original = str(row["original"])
        out.append(
            Entity(
                original=original,
                entity_type=EntityType(str(row["entity_type"])),
                category=EntityCategory(str(row["entity_category"])),
                confidence=float(row["confidence"]),
                detection_layer=DetectionLayer(str(row["detection_layer"])),
                pseudonym=str(row["pseudonym"]),
                effective_mode=PseudonymizationMode(str(row["pseudonymization_mode"])),
                start=0,
                end=len(original),
            )
        )
    return out


def replace_two_way_pseudonyms(text: str, pseudonym_to_original: list[tuple[str, str]]) -> str:
    """Vervang non-overlappend greedily; pseudoniemen langste eerst."""
    ordered = sorted(pseudonym_to_original, key=lambda p: len(p[0]), reverse=True)
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched: tuple[int, str] | None = None
        for pseudo, orig in ordered:
            if text.startswith(pseudo, i):
                matched = (len(pseudo), orig)
                break
        if matched is not None:
            ln, orig = matched
            out.append(orig)
            i += ln
        else:
            out.append(text[i])
            i += 1
    return "".join(out)
