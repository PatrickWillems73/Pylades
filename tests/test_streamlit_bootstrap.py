"""Regressie: Streamlit-entrypoints — sys.path-shim en navigatie-shell."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAGE_SCRIPTS = (
    ROOT / "ui" / "views" / "0_Home.py",
    ROOT / "ui" / "views" / "1_Status.py",
    ROOT / "ui" / "views" / "2_Opdrachten.py",
    ROOT / "ui" / "views" / "3_Review_Queue.py",
    ROOT / "ui" / "views" / "4_Audit.py",
    ROOT / "ui" / "views" / "5_Config.py",
)


def test_all_page_scripts_contain_path_shim() -> None:
    for path in PAGE_SCRIPTS:
        source = path.read_text(encoding="utf-8")
        assert "pyproject.toml" in source, (
            f"{path.name} mist de sys.path-shim — `streamlit run` zal "
            "ModuleNotFoundError op `ui.*` of `shared.*` geven."
        )
        assert "sys.path.insert(0, str(_root))" in source, (
            f"{path.name}: shim is verminkt — voeg een walk-up naar pyproject.toml "
            "+ sys.path.insert toe."
        )


def test_home_entry_uses_hidden_navigation() -> None:
    source = (ROOT / "ui" / "Home.py").read_text(encoding="utf-8")
    assert "st.navigation" in source
    assert 'position="hidden"' in source
    assert "st.page_link" in source
    assert "init_pylades_ui()" in source


def test_subpages_do_not_duplicate_ui_shell() -> None:
    for path in PAGE_SCRIPTS:
        source = path.read_text(encoding="utf-8")
        assert "init_pylades_ui" not in source, (
            f"{path.name} roept init_pylades_ui aan — hoort alleen in ui/Home.py."
        )
        assert "pylades_set_page_config" not in source, (
            f"{path.name} roept pylades_set_page_config aan — hoort alleen in ui/Home.py."
        )


def test_home_uses_navigation_page_for_review_redirect() -> None:
    source = (ROOT / "ui" / "views" / "0_Home.py").read_text(encoding="utf-8")
    assert "REVIEW_QUEUE_PAGE" in source
    assert "mark_review_return_home" in source
    assert "SCROLL_TO_HOME_ACTION_KEY" in source
    assert 'switch_page("pages/' not in source


def test_review_queue_auto_returns_home_after_resolved() -> None:
    source = (ROOT / "ui" / "views" / "3_Review_Queue.py").read_text(encoding="utf-8")
    assert "try_return_home_after_review" in source


def test_config_hides_auto_sidebar_nav() -> None:
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "showSidebarNavigation = false" in config
