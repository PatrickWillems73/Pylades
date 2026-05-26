"""Pylades — Streamlit-entrypoint.

Gebruikt ``st.navigation(position="hidden")`` + handmatige ``st.page_link`` in de
sidebar. Voorkomt sidebar-flicker bij paginawissel (Streamlit MPAv1 ``pages/``-nav
herlaadt de sidebar bij elke klik).

Start: ``uv run streamlit run ui/Home.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

for _root in Path(__file__).resolve().parents:
    if (_root / "pyproject.toml").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

import streamlit as st

from shared.db import init_databases
from ui.navigation import PYLADES_PAGES
from ui.ui_extras import init_pylades_ui, pylades_set_page_config, render_sidebar_branding

init_databases()

pylades_set_page_config("Home", default_collapsed=True)
init_pylades_ui()

pg = st.navigation(PYLADES_PAGES, position="hidden")

with st.sidebar:
    render_sidebar_branding()
    for page in PYLADES_PAGES:
        st.page_link(page)

pg.run()
