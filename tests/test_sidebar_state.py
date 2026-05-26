"""Sidebar-state script — regressie op module en wiring."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_state_module_contains_streamlit_storage_key() -> None:
    source = (ROOT / "ui" / "sidebar_state.py").read_text(encoding="utf-8")
    assert "stSidebarCollapsed-" in source
    assert "pylades_sidebar_user_open" in source
    assert "__pyladesSidebarV2" in source


def test_init_pylades_ui_applies_sidebar_state() -> None:
    source = (ROOT / "ui" / "ui_extras.py").read_text(encoding="utf-8")
    assert "apply_sidebar_state" in source
    assert "st.logo" in source
    assert "icon_image" in source


def test_navigation_module_defines_all_pages() -> None:
    source = (ROOT / "ui" / "navigation.py").read_text(encoding="utf-8")
    for name in (
        "0_Home.py",
        "1_Status.py",
        "2_Prompts.py",
        "3_Review_Queue.py",
        "4_Audit.py",
        "5_Config.py",
    ):
        assert name in source
