"""Pylades — Audit-pagina.

Overzicht van recente proxy-requests + detail-view met vier tabs:
Origineel, Pseudonimized, Response-pseudonimized, Response-terug. De
pagina leest uitsluitend de content-DB (audit_log) — vault-data komt
hier nooit door (BR-G02).
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` zet alleen de script-directory op sys.path; zonder shim is
# `ui.*` of `shared.*` niet importeerbaar vanuit deze entry-pagina.
for _root in Path(__file__).resolve().parents:
    if (_root / "pyproject.toml").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

import streamlit as st

from proxy.audit import get_log_by_id, get_logs_by_session, get_recent_logs
from shared.models import AuditEntry
from ui.audit_format import pretty_json, status_badge
from ui.ui_extras import section_spacer

st.title("Audit")
st.caption(
    "Append-only log van alle proxy-calls (BR-G01). Geen vault-data hier — "
    "pseudoniem-mappings staan in de aparte vault-DB."
)

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

with st.container(border=True):
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_a:
        limit = st.slider("Aantal recente entries", 10, 500, 50, 10, key="audit-limit")
    with col_b:
        session_filter = st.text_input(
            "Filter op session-id (optioneel)",
            value="",
            key="audit-session-filter",
        ).strip()
    with col_c:
        st.write("")  # spacer
        st.write("")
        if st.button("Ververs", use_container_width=True, key="audit-refresh"):
            st.rerun()

if session_filter:
    entries = get_logs_by_session(session_filter)
    entries = list(reversed(entries))  # nieuwste eerst, consistent met overzicht
else:
    entries = get_recent_logs(limit=limit)

# ---------------------------------------------------------------------------
# Overzicht
# ---------------------------------------------------------------------------

if not entries:
    st.info("Geen audit-rijen gevonden voor deze filter.")
    st.stop()


def _short(value: str | None, n: int = 60) -> str:
    if value is None:
        return ""
    flat = value.replace("\n", " ")
    return flat if len(flat) <= n else flat[: n - 1] + "…"


def _row_dict(entry: AuditEntry) -> dict[str, str]:
    badge = status_badge(entry)
    return {
        "id": str(entry.id) if entry.id is not None else "",
        "tijd": (
            entry.created_at.strftime("%Y-%m-%d %H:%M:%S") if entry.created_at is not None else ""
        ),
        "status": badge.label,
        "session": entry.session_id[:8] + "…" if len(entry.session_id) > 8 else entry.session_id,
        "template_id": str(entry.template_id) if entry.template_id is not None else "",
        "provider/model": f"{entry.llm_provider or '—'} / {entry.llm_model or '—'}",
        "avg_conf": (f"{entry.avg_confidence:.2f}" if entry.avg_confidence is not None else "—"),
        "prompt (snippet)": _short(entry.original_prompt),
    }


st.subheader(f"Recente requests ({len(entries)})")
st.dataframe(
    [_row_dict(e) for e in entries],
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------------------
# Detail-view
# ---------------------------------------------------------------------------

section_spacer()
st.subheader("Detail")

option_labels = {
    f"#{e.id} · {e.session_id[:8]} · {status_badge(e).label}": e.id
    for e in entries
    if e.id is not None
}

if not option_labels:
    st.info("Geen entries om in detail te tonen.")
    st.stop()

picked = st.selectbox(
    "Kies een entry",
    list(option_labels.keys()),
    key="audit-detail-select",
)
entry_id = option_labels[picked]
entry = get_log_by_id(entry_id)

if entry is None:
    st.error(f"Entry #{entry_id} bestaat niet meer (verwijderd?). Ververs de pagina.")
    st.stop()

badge = status_badge(entry)
status_render = {
    "error": st.error,
    "warning": st.warning,
    "success": st.success,
    "neutral": st.info,
}.get(badge.tone, st.info)

status_render(
    f"Status: **{badge.label}** · "
    f"Session `{entry.session_id}` · "
    f"Provider/model `{entry.llm_provider or '—'}` / `{entry.llm_model or '—'}`"
)
if entry.error:
    st.error(f"Fout-bericht: {entry.error}")

tabs = st.tabs(
    [
        "Origineel",
        "Pseudonimized (naar LLM)",
        "Response (pseud)",
        "Response (terug)",
    ]
)

with tabs[0]:
    st.markdown("Exact wat de gebruiker instuurde, vóór generalisering of pseudonimisering.")
    st.code(entry.original_prompt, language="text")

with tabs[1]:
    st.markdown("Wat upstream daadwerkelijk te zien kreeg.")
    st.code(entry.pseudonymized_prompt, language="text")

with tabs[2]:
    st.markdown("Letterlijke response van de upstream LLM (pseudoniemen nog ingevuld).")
    if entry.response_pseudonymized is None:
        st.info("Geen response geregistreerd (request bleek review-required of upstream-fout).")
    else:
        st.code(pretty_json(entry.response_pseudonymized), language="json")

with tabs[3]:
    st.markdown("Response na selectieve TWO_WAY-terugvertaling — wat de client kreeg.")
    if entry.response_depseudonymized is None:
        st.info("Geen terug-vertaalde response (request kreeg geen upstream-200).")
    else:
        st.code(pretty_json(entry.response_depseudonymized), language="json")
