"""SQLite-connecties en schema-init voor de twee gescheiden databases.

Twee context managers (`get_content_connection`, `get_vault_connection`) en
een idempotente `init_databases()`. Context managers ipv één globale
connection omdat SQLite's WAL-mode connection-affiniteit per thread
verwacht; een gedeelde globale connection zou stilletjes falen wanneer
FastAPI's threadpool een handler op een andere thread plant dan waar de
connection geopend werd.

Importeert nooit uit `proxy/` of `ui/`; die kant op blijft de DAG schoon.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from shared.config import settings

# Permissies op de vault-file: BR-G02 vereist owner-only toegang.
_VAULT_FILE_MODE = 0o600


# ---------------------------------------------------------------------------
# Schemas (inline; klein genoeg om naast de gebruikers te leven)
# ---------------------------------------------------------------------------

_CONTENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    groep TEXT NOT NULL,
    naam TEXT NOT NULL,
    beschrijving TEXT NOT NULL DEFAULT '',
    llm_provider TEXT NOT NULL,
    llm_naam TEXT NOT NULL,
    prompt_tekst TEXT NOT NULL DEFAULT '',
    max_tokens INTEGER NOT NULL DEFAULT 16000,
    use_llm INTEGER NOT NULL DEFAULT 0,
    default_mode TEXT,
    mode_overrides TEXT NOT NULL DEFAULT '{}',
    two_way_justification TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    template_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (template_id) REFERENCES templates(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    template_id INTEGER,
    original_prompt TEXT NOT NULL,
    pseudonymized_prompt TEXT NOT NULL,
    response_pseudonymized TEXT,
    response_depseudonymized TEXT,
    llm_provider TEXT,
    llm_model TEXT,
    avg_confidence REAL,
    review_required INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (template_id) REFERENCES templates(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    original_text TEXT NOT NULL,
    detected_text TEXT NOT NULL,
    proposed_entity_type TEXT NOT NULL,
    proposed_category TEXT NOT NULL,
    confidence REAL NOT NULL,
    detection_layer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    user_decision_entity_type TEXT,
    user_decision_at TEXT,
    user_decision_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id);
CREATE INDEX IF NOT EXISTS idx_review_session_status
    ON review_queue(session_id, status);
"""

_VAULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    pseudonym TEXT NOT NULL,
    original TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_category TEXT NOT NULL,
    pseudonymization_mode TEXT NOT NULL,
    confidence REAL NOT NULL,
    detection_layer TEXT NOT NULL,
    generalized_to TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, pseudonym),
    UNIQUE(session_id, original, entity_type)
);

CREATE INDEX IF NOT EXISTS idx_mappings_session ON mappings(session_id);
"""


# ---------------------------------------------------------------------------
# Connection-context managers
# ---------------------------------------------------------------------------


def _open(path: Path) -> sqlite3.Connection:
    # Aparte private opener zodat beide publieke context managers
    # *exact* dezelfde pragma's en row-factory toepassen — geen drift.
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # FKs zijn in SQLite per-connection en standaard uit; expliciet aanzetten.
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL persisteert in de DB-file, maar opnieuw uitvoeren is veilig en
    # garandeert dat read-only-readers (UI) niet blokkeren op de writer (proxy).
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_content_connection() -> Iterator[sqlite3.Connection]:
    """Open een transactie op de content-database.

    Commit bij normale exit, rollback bij elke exception, sluit altijd.
    Mag NOOIT geïmporteerd worden vanuit `proxy/mapping.py` (BR-G02; bewaakt
    door `tests/test_db_separation.py`).
    """
    conn = _open(settings.content_db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_vault_connection() -> Iterator[sqlite3.Connection]:
    """Open een transactie op de vault-database.

    Mag NOOIT geïmporteerd worden vanuit `proxy/audit.py` (BR-G02; bewaakt
    door `tests/test_db_separation.py`).
    """
    conn = _open(settings.vault_db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Initialisatie
# ---------------------------------------------------------------------------


def _ensure_templates_columns(conn: sqlite3.Connection) -> None:
    """Idempotente ALTER's voor kolommen die ná de eerste schema-rev zijn
    bijgekomen. SQLite ondersteunt geen `ADD COLUMN IF NOT EXISTS`, dus
    inspecteren we `PRAGMA table_info` zelf — goedkoper dan een try/except
    op een specifieke OperationalError-text en robuust over SQLite-versies.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(templates)").fetchall()}
    if "max_tokens" not in cols:
        conn.execute("ALTER TABLE templates ADD COLUMN max_tokens INTEGER NOT NULL DEFAULT 16000")
    if "use_llm" not in cols:
        conn.execute("ALTER TABLE templates ADD COLUMN use_llm INTEGER NOT NULL DEFAULT 0")
    if "sort_order" not in cols:
        conn.execute("ALTER TABLE templates ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        rows = conn.execute(
            "SELECT id FROM templates ORDER BY groep ASC, naam ASC, id ASC"
        ).fetchall()
        for idx, row in enumerate(rows):
            conn.execute(
                "UPDATE templates SET sort_order = ? WHERE id = ?",
                (idx, int(row["id"])),
            )


def _init_content_db() -> None:
    with get_content_connection() as conn:
        conn.executescript(_CONTENT_SCHEMA)
        _ensure_templates_columns(conn)


def _init_vault_db() -> None:
    with get_vault_connection() as conn:
        conn.executescript(_VAULT_SCHEMA)


def init_databases() -> None:
    """Maak beide databases met schema's en zet vault-file op 0o600.

    Idempotent: gebruikt `CREATE TABLE IF NOT EXISTS`. Veilig om bij elke
    proxy-start aan te roepen; veroorzaakt geen migration-conflicts zolang
    we niet aan bestaande kolommen sleutelen.
    """
    _init_content_db()
    _init_vault_db()

    # chmod *na* SQLite het bestand heeft aangemaakt; SQLite zelf
    # respecteert umask. Defensief zetten in plaats van zich erop verlaten.
    if settings.vault_db_path.exists():
        settings.vault_db_path.chmod(_VAULT_FILE_MODE)


# ---------------------------------------------------------------------------
# Config-helpers (alleen content-db)
# ---------------------------------------------------------------------------


def get_config_value(key: str, default: str | None = None) -> str | None:
    """Lees een runtime-config-waarde als string of `default` als niet gezet.

    Callers castten naar float/int/bool waar nodig — bewust géén generieke
    type-cast hier, omdat het type per key context-afhankelijk is.
    """
    with get_content_connection() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    value: str = row["value"]
    return value


def set_config_value(key: str, value: str) -> None:
    """Schrijf of overschrijf een runtime-config-waarde (upsert)."""
    with get_content_connection() as conn:
        conn.execute(
            "INSERT INTO config (key, value, updated_at) "
            "VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at",
            (key, value),
        )
