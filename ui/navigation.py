"""st.navigation-pagina's — één bron voor labels en url_path."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

_UI = Path(__file__).resolve().parent
_PAGES = _UI / "views"

PYLADES_PAGES: list[st.Page] = [
    st.Page(_PAGES / "0_Home.py", title="Home", default=True, url_path=""),
    st.Page(_PAGES / "1_Status.py", title="Status", url_path="Status"),
    st.Page(_PAGES / "2_Opdrachten.py", title="Opdrachten", url_path="Opdrachten"),
    st.Page(
        _PAGES / "3_Review_Queue.py",
        title="Review Queue",
        url_path="Review_Queue",
    ),
    st.Page(_PAGES / "4_Audit.py", title="Audit", url_path="Audit"),
    st.Page(_PAGES / "5_Config.py", title="Config", url_path="Config"),
]

REVIEW_QUEUE_PAGE = PYLADES_PAGES[3]
HOME_PAGE = PYLADES_PAGES[0]
