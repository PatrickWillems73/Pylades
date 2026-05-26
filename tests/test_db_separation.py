"""BR-G02-bewaking: content-db en vault-db zijn strikt gescheiden.

Twee soorten checks:

1. **Statisch** (AST): `proxy/audit.py` mag `get_vault_connection` nooit
   noemen en `proxy/mapping.py` mag `get_content_connection` nooit noemen.
   Deze checks worden geschreven *voordat* die modules bestaan (stap 7/9);
   tot dan skippen ze zichzelf met een duidelijke melding.

2. **Runtime**: na `init_databases()` bestaan twee aparte bestanden, het
   vault-bestand heeft mode 0o600, en geen van beide schema's bevat
   tabellen van de andere.

Beide samen dekken acceptatiecriterium 1-4 onder BR-G02.
"""

import ast
import os
import sqlite3
from pathlib import Path

import pytest

from shared.config import settings
from shared.db import init_databases

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# AST-helpers
# ---------------------------------------------------------------------------


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _referenced_names(tree: ast.Module) -> set[str]:
    """Alle namen die op enige manier in de module 'aangeroepen' worden.

    Pakt `Name` (bare reference), `Attribute.attr` (dotted access),
    en alle namen uit `import ...` en `from ... import ...`. Dit is breed
    genoeg dat zowel `from shared.db import get_vault_connection` als
    `shared.db.get_vault_connection()` of `db.get_vault_connection()` wordt
    gevangen.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
    return names


def _top_level_definitions(tree: ast.Module) -> set[str]:
    """Top-level functies, klassen en assignments — gebruikt om te bewijzen
    dat `shared/db.py` *zelf* beide helpers exporteert."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


# ---------------------------------------------------------------------------
# Statisch: AST-bewaking
# ---------------------------------------------------------------------------


class TestStaticSeparation:
    """Code in `proxy/` kruist niet over de DB-grens (BR-G02 criterium 4)."""

    def test_audit_does_not_reference_vault_connection(self) -> None:
        audit_path = PROJECT_ROOT / "proxy" / "audit.py"
        if not audit_path.exists():
            pytest.skip("proxy/audit.py bestaat nog niet (volgt in stap 9)")

        refs = _referenced_names(_parse(audit_path))
        leaked = refs & {"get_vault_connection"}
        assert not leaked, (
            f"proxy/audit.py mag de vault nooit aanraken (BR-G02), "
            f"maar refereert aan: {sorted(leaked)}"
        )

    def test_mapping_does_not_reference_content_connection(self) -> None:
        mapping_path = PROJECT_ROOT / "proxy" / "mapping.py"
        if not mapping_path.exists():
            pytest.skip("proxy/mapping.py bestaat nog niet (volgt in stap 7)")

        refs = _referenced_names(_parse(mapping_path))
        leaked = refs & {"get_content_connection"}
        assert not leaked, (
            f"proxy/mapping.py mag de content-db nooit aanraken (BR-G02), "
            f"maar refereert aan: {sorted(leaked)}"
        )

    def test_db_module_exports_both_helpers(self) -> None:
        # Sanity: zonder dit zou de check hierboven valse veiligheid geven
        # (als `get_vault_connection` per ongeluk niet eens gedefinieerd was,
        # zou de audit-check trivialiter slagen).
        db_path = PROJECT_ROOT / "shared" / "db.py"
        defs = _top_level_definitions(_parse(db_path))
        assert "get_content_connection" in defs, "shared/db.py mist get_content_connection"
        assert "get_vault_connection" in defs, "shared/db.py mist get_vault_connection"


# ---------------------------------------------------------------------------
# Runtime-invariants
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_databases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    # We werken op `settings` in plaats van op een argument naar
    # `init_databases()` zodat we precies het pad bewandelen dat de echte
    # applicatie ook neemt — geen test-only code-pad.
    content_path = tmp_path / "test-content.db"
    vault_path = tmp_path / "test-vault.db"
    monkeypatch.setattr(settings, "content_db_path", content_path)
    monkeypatch.setattr(settings, "vault_db_path", vault_path)

    init_databases()
    return content_path, vault_path


class TestRuntimeSeparation:
    """Na `init_databases()` gelden de fysieke en schematische invarianten."""

    def test_two_separate_files_exist(self, temp_databases: tuple[Path, Path]) -> None:
        content_path, vault_path = temp_databases
        assert content_path.exists(), "content-db ontbreekt"
        assert vault_path.exists(), "vault-db ontbreekt"
        assert content_path.resolve() != vault_path.resolve(), (
            "BR-G02 vereist twee fysiek aparte files; deze wijzen naar dezelfde inode"
        )

    def test_vault_has_owner_only_permissions(self, temp_databases: tuple[Path, Path]) -> None:
        _, vault_path = temp_databases
        mode = vault_path.stat().st_mode & 0o777
        assert mode == 0o600, f"vault-file moet 0o600 zijn (BR-G02), is 0o{mode:o}"

    def test_content_db_has_no_mapping_tables(self, temp_databases: tuple[Path, Path]) -> None:
        content_path, _ = temp_databases
        with sqlite3.connect(content_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        assert "mappings" not in tables, (
            f"content-db mag geen 'mappings' bevatten (BR-G02), zag: {sorted(tables)}"
        )

    def test_vault_db_has_no_content_tables(self, temp_databases: tuple[Path, Path]) -> None:
        _, vault_path = temp_databases
        with sqlite3.connect(vault_path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        forbidden = {"audit_log", "templates", "sessions", "review_queue", "config"}
        leaked = tables & forbidden
        assert not leaked, f"vault-db bevat content-tabellen (BR-G02): {sorted(leaked)}"


# ---------------------------------------------------------------------------
# Sanity-check op test-omgeving zelf
# ---------------------------------------------------------------------------


def test_project_root_is_correct() -> None:
    # Voorkom dat de hele suite stilletjes skipt doordat PROJECT_ROOT
    # ergens anders heen wijst dan we denken.
    assert (PROJECT_ROOT / "pyproject.toml").exists(), (
        f"PROJECT_ROOT lijkt verkeerd te staan: {PROJECT_ROOT}"
    )
    # Cleanup-hint voor lokale runs: laat geen lingering env-shadow zien.
    assert "PYLADES_TEST_ENV" not in os.environ
