"""Helpers voor Review-queue sessie-selectie (testbaar zonder Streamlit-page-import)."""

from __future__ import annotations

import streamlit as st


def resolve_session_id(*, manual: str, picked: str, override: str, active: str) -> str:
    """Kies session-id: handmatig > dropdown > deeplink > laatst actieve sessie."""
    return manual.strip() or picked or override.strip() or active.strip()


def seed_manual_session_widget(session_id: str) -> None:
    """Vul het handveld vóór render als de dropdown leeg is (widget-state)."""
    if not session_id:
        return
    current = str(st.session_state.get("review-session-manual", ""))
    if not current.strip():
        st.session_state["review-session-manual"] = session_id
