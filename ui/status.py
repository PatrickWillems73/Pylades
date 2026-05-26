"""Statuscontroles voor de Streamlit-homepage.

Pure functies zonder Streamlit-imports, zodat ze in pytest direct
aangeroepen kunnen worden zonder een `streamlit run`-context. Elke check
levert een `StatusCheck`-record met (a) of het ok is, (b) een korte
gebruikersboodschap en (c) een shell-command om het probleem op te lossen
als het niet ok is.

Korte timeouts (1s) per netwerkcheck: de homepage moet ook draaien op een
half-kapotte machine zonder de gebruiker tien seconden te laten wachten.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import httpx

from shared.config import settings


@dataclass(frozen=True)
class StatusCheck:
    """Eén bolletje + tekst + fix-commando voor de UI-homepage."""

    name: str
    ok: bool
    message: str
    fix_command: str | None = None
    fix_hint: str | None = None


_NET_TIMEOUT_SECONDS = 1.0


def check_proxy() -> StatusCheck:
    """Pingt `proxy.main:app` via `/healthz` op `localhost:<proxy_port>`."""
    url = f"http://127.0.0.1:{settings.proxy_port}/healthz"
    try:
        response = httpx.get(url, timeout=_NET_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return StatusCheck(
            name="Proxy",
            ok=False,
            message=f"Niet bereikbaar op {url} ({type(exc).__name__})",
            fix_command=f"uv run uvicorn proxy.main:app --port {settings.proxy_port}",
            fix_hint="Start de Pylades-proxy in een andere terminal.",
        )
    if response.status_code != 200:
        return StatusCheck(
            name="Proxy",
            ok=False,
            message=f"Onverwachte status {response.status_code} op /healthz",
            fix_command=f"uv run uvicorn proxy.main:app --port {settings.proxy_port}",
            fix_hint="Herstart de proxy en controleer de logs.",
        )
    return StatusCheck(name="Proxy", ok=True, message=f"OK op poort {settings.proxy_port}")


def check_ollama() -> StatusCheck:
    """Pingt Ollama via `/api/tags`; modelchecks zijn voor laag-3-detectie."""
    url = f"{settings.ollama_host.rstrip('/')}/api/tags"
    try:
        response = httpx.get(url, timeout=_NET_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        return StatusCheck(
            name="Ollama",
            ok=False,
            message=f"Niet bereikbaar op {settings.ollama_host} ({type(exc).__name__})",
            fix_command=f"ollama serve\nollama pull {settings.ollama_model}",
            fix_hint=(
                "Ollama is alleen nodig als je laag-3-detectie wilt gebruiken; "
                "zonder Ollama draait de pijplijn met regex + spaCy."
            ),
        )
    if response.status_code != 200:
        return StatusCheck(
            name="Ollama",
            ok=False,
            message=f"Onverwachte status {response.status_code} op /api/tags",
            fix_command=f"ollama pull {settings.ollama_model}",
            fix_hint="Inspecteer Ollama-logs en download eventueel het model.",
        )
    try:
        data = response.json()
        names = {item.get("name") for item in data.get("models", [])}
    except ValueError:
        names = set()
    if settings.ollama_model not in names:
        return StatusCheck(
            name="Ollama",
            ok=False,
            message=f"Model {settings.ollama_model!r} niet aanwezig",
            fix_command=f"ollama pull {settings.ollama_model}",
            fix_hint="Ollama draait, maar het ingestelde model is nog niet gepulld.",
        )
    return StatusCheck(name="Ollama", ok=True, message=f"OK; model {settings.ollama_model}")


def check_spacy() -> StatusCheck:
    """Controleert of `spacy.util.is_package(spacy_model)` waar is.

    Volle `spacy.load()` zou ~5s kosten op de M1; voor een statuscard is dat
    onnodig — een installatiecheck is voldoende voor de UI.
    """
    try:
        import spacy  # noqa: PLC0415
    except ImportError:
        return StatusCheck(
            name="spaCy",
            ok=False,
            message="spaCy-package niet geïnstalleerd",
            fix_command="uv sync",
            fix_hint="Voer `uv sync` uit om alle dependencies te installeren.",
        )
    if not spacy.util.is_package(settings.spacy_model):
        return StatusCheck(
            name="spaCy",
            ok=False,
            message=f"Model {settings.spacy_model!r} niet geïnstalleerd",
            fix_command=f"uv run python -m spacy download {settings.spacy_model}",
            fix_hint="Het NL-model is een aparte download; ~50MB.",
        )
    return StatusCheck(name="spaCy", ok=True, message=f"OK; model {settings.spacy_model}")


def _can_open_sqlite(path: Path) -> bool:
    """Bestand bestaat én SQLite kan er een trivial query op draaien."""
    if not path.exists():
        return False
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.DatabaseError:
        return False


def check_databases() -> StatusCheck:
    """Beide DB-files bestaan en zijn openbaar voor `sqlite3.connect`."""
    content_ok = _can_open_sqlite(settings.content_db_path)
    vault_ok = _can_open_sqlite(settings.vault_db_path)
    if content_ok and vault_ok:
        return StatusCheck(
            name="Databases",
            ok=True,
            message="content-db en vault-db beschikbaar",
        )
    missing: list[str] = []
    if not content_ok:
        missing.append(f"content ({settings.content_db_path})")
    if not vault_ok:
        missing.append(f"vault ({settings.vault_db_path})")
    return StatusCheck(
        name="Databases",
        ok=False,
        message="Ontbreekt of niet leesbaar: " + ", ".join(missing),
        fix_command='uv run python -c "from shared.db import init_databases; init_databases()"',
        fix_hint="Initialiseer beide SQLite-files met het verwachte schema.",
    )


def run_all_checks() -> list[StatusCheck]:
    """Voer alle vier statuscontroles uit in vaste volgorde."""
    return [check_proxy(), check_ollama(), check_spacy(), check_databases()]
