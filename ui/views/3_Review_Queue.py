"""Pylades — Review-queue-pagina.

Per pending entiteit: context-snippet (5 woorden voor/na), accept/modify/
reject met optionele note. Zodra alle items in de sessie resolved zijn,
toont de pagina een "Hervat sessie"-paneel met het `resume_session`-body-
veld dat de client moet meesturen — de UI doet zelf geen proxy-call, de
gebruiker behoudt controle over zijn oorspronkelijke prompt.
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

from proxy.review import (
    all_resolved,
    decide,
    get_pending,
    list_sessions_with_pending,
)
from shared.models import EntityType, ReviewStatus
from ui.navigation import PYLADES_PAGES
from ui.review_flow import try_return_home_after_review
from ui.review_queue_helpers import resolve_session_id, seed_manual_session_widget
from ui.review_snippet import make_context_snippet
from ui.ui_extras import section_spacer

_ACTIVE_SESSION_KEY = "review_active_session"

st.title("Review-queue")
st.caption(
    "Items met confidence onder de threshold of een rare-ICD-flag wachten hier "
    "op een menselijke beslissing voordat de prompt verder mag (BR-A04)."
)

# ---------------------------------------------------------------------------
# Sessie-selectie
# ---------------------------------------------------------------------------

sessions = list_sessions_with_pending()

if "review_session_override" not in st.session_state:
    st.session_state.review_session_override = ""
if _ACTIVE_SESSION_KEY not in st.session_state:
    st.session_state[_ACTIVE_SESSION_KEY] = ""

# Deeplink-pad: de Home/Testrun-pagina kan ons een sessie meegeven via
# `?session=<sid>` of `st.session_state["review_session_override"]`. We
# laten de query-param leidend zijn (bookmarkable), maar promoveren hem
# eenmalig naar de state-key zodat refresh-on-the-page niet de seed wist.
_deeplink_sid = st.query_params.get("session", "")
if _deeplink_sid and _deeplink_sid != st.session_state.review_session_override:
    st.session_state.review_session_override = _deeplink_sid

with st.container(border=True):
    st.subheader("Selecteer sessie")
    override = st.session_state.review_session_override
    if sessions:
        labels = [f"{sid}  ({count} openstaand)" for sid, count in sessions]
        # Pre-select de overridden sessie als die in de lijst staat; anders 0.
        idx_default = 0
        if override:
            for idx, (sid, _count) in enumerate(sessions):
                if sid == override:
                    idx_default = idx
                    break
        selected_label = st.selectbox(
            "Sessies met openstaande items (oudste eerst)",
            labels,
            index=idx_default,
            key="review-session-select",
        )
        picked_session = sessions[labels.index(selected_label)][0]
        st.session_state[_ACTIVE_SESSION_KEY] = picked_session
    else:
        active = st.session_state[_ACTIVE_SESSION_KEY]
        if active:
            st.info(
                "Geen sessies met openstaande items meer. "
                f"Status van `{active}` staat hieronder."
            )
        else:
            st.info("Geen sessies met openstaande review-items.")
        picked_session = ""
        seed_manual_session_widget(active or override)

    manual = st.text_input(
        "Of plak een session-id (bijv. resolved sessies om de status te zien)",
        key="review-session-manual",
    )
    session_id = resolve_session_id(
        manual=manual,
        picked=picked_session,
        override=override,
        active=st.session_state[_ACTIVE_SESSION_KEY],
    )
    if session_id:
        st.session_state[_ACTIVE_SESSION_KEY] = session_id

if not session_id:
    st.stop()

section_spacer()
st.markdown(f"**Sessie:** `{session_id}`")

# ---------------------------------------------------------------------------
# Pending items
# ---------------------------------------------------------------------------

pending = get_pending(session_id)

if not pending:
    try_return_home_after_review(session_id)


def _entity_type_options() -> list[EntityType]:
    return list(EntityType)


def _render_item_card(item_id: int) -> None:
    item = next((i for i in pending if i.id == item_id), None)
    if item is None or item.id is None:
        st.warning("Item is verdwenen — herlaad de pagina.")
        return

    snippet = make_context_snippet(item.original_text, item.detected_text, words=5)

    with st.container(border=True):
        cols = st.columns([3, 2])
        with cols[0]:
            st.markdown(
                f"**Gedetecteerd:** `{item.detected_text}`  \n"
                f"**Voorgesteld type:** `{item.proposed_entity_type.value}` · "
                f"categorie `{item.proposed_category.value}`  \n"
                f"**Confidence:** {item.confidence:.2f} · "
                f"**Laag:** `{item.detection_layer.value}`"
            )
            prefix = "… " if snippet.truncated_before else ""
            suffix = " …" if snippet.truncated_after else ""
            st.markdown(
                f"> {prefix}{snippet.before} **:orange[{snippet.match}]** {snippet.after}{suffix}"
            )

        with cols[1]:
            note = st.text_input(
                "Note (optioneel)",
                key=f"note-{item.id}",
                placeholder="bv. team-naam, geen persoon",
            )
            type_options = _entity_type_options()
            default_index = type_options.index(item.proposed_entity_type)
            new_type = st.selectbox(
                "Wijzig type (alleen voor Modify)",
                type_options,
                index=default_index,
                format_func=lambda t: t.value,
                key=f"type-{item.id}",
            )

            action_cols = st.columns(3)
            if action_cols[0].button("Accept", key=f"acc-{item.id}"):
                decide(item.id, ReviewStatus.ACCEPTED, note=note or None)
                try_return_home_after_review(session_id)
                st.rerun()
            if action_cols[1].button("Modify", key=f"mod-{item.id}"):
                if new_type is item.proposed_entity_type:
                    st.warning("Kies een ander type voor 'Modify', anders gebruik 'Accept'.")
                else:
                    decide(
                        item.id,
                        ReviewStatus.MODIFIED,
                        modified_type=new_type,
                        note=note or None,
                    )
                    try_return_home_after_review(session_id)
                    st.rerun()
            if action_cols[2].button("Reject", key=f"rej-{item.id}"):
                decide(item.id, ReviewStatus.REJECTED, note=note or None)
                try_return_home_after_review(session_id)
                st.rerun()


if pending:
    st.subheader(f"Openstaande items: {len(pending)}")
    for item in pending:
        if item.id is None:
            continue
        _render_item_card(item.id)
else:
    st.success("Geen openstaande items voor deze sessie.")

# ---------------------------------------------------------------------------
# Resume-paneel
# ---------------------------------------------------------------------------

section_spacer()

if all_resolved(session_id):
    st.session_state.testrun_session_id = session_id
    st.success("Alle review-items zijn afgehandeld — je wordt teruggeleid naar Home…")
    try_return_home_after_review(session_id)
    st.page_link(
        PYLADES_PAGES[0],
        label="Terug naar Home om te hervatten",
        icon=":material/home:",
    )
    st.markdown(
        "**Of hervat via de API.** Herstuur je oorspronkelijke request naar "
        "`POST /v1/messages` met dezelfde `template_id` + `dossier` en het "
        "extra body-veld `resume_session`:"
    )
    st.code(
        "{\n"
        '  "template_id": <id>,\n'
        '  "dossier": "<oorspronkelijke dossier-tekst>",\n'
        f'  "resume_session": "{session_id}"\n'
        "}",
        language="json",
    )
    st.caption(
        "De proxy past je beslissingen toe (accepted / modified / rejected) "
        "en gaat dan door met generaliseren, pseudonimiseren en upstream-call."
    )
else:
    st.info("Sessie is nog niet hervatbaar — er staan nog PENDING-items open.")
