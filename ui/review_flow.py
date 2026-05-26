"""Home ↔ Review-queue navigatie na testrun (BR-A04 follow-up)."""

from __future__ import annotations

import streamlit as st

from proxy.review import all_resolved
from ui.navigation import HOME_PAGE

REVIEW_RETURN_HOME_KEY = "_review_return_home"
SCROLL_TO_HOME_ACTION_KEY = "_scroll_to_home_action"
HOME_ACTION_ANCHOR_ID = "pylades-home-next-action"


def mark_review_return_home() -> None:
    """Markeer dat Review-queue terug moet naar Home zodra alles resolved is."""
    st.session_state[REVIEW_RETURN_HOME_KEY] = True


def try_return_home_after_review(session_id: str) -> None:
    """Spring terug naar Home + scroll-flag wanneer review voor deze sessie klaar is."""
    if not st.session_state.get(REVIEW_RETURN_HOME_KEY):
        return
    if not all_resolved(session_id):
        return
    st.session_state.testrun_session_id = session_id
    st.session_state[SCROLL_TO_HOME_ACTION_KEY] = True
    st.session_state.pop(REVIEW_RETURN_HOME_KEY, None)
    st.session_state.pop("review_session_override", None)
    if "session" in st.query_params:
        del st.query_params["session"]
    st.switch_page(HOME_PAGE)
