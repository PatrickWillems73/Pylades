"""Pylades — Home/Testrun-pagina.

Sinds v0.2.0 is de testrun-flow de homepagina: hij is voor alle doelgroepen
het belangrijkste interactiepunt. Een modus-schakelaar bovenaan
(Eenvoudig / Uitgebreid) bepaalt of we de plain-language-flow tonen
(samenvattingskaart, entiteit-kaartjes, bewuste tweeklik vóór de echte
upstream-call) of de huidige diagnostics (mapping-tabel, JSON-response,
curl-equivalent, raw upstream-body, latency). Onder de motorkap zit één
gedeelde pijplijn — alleen de presentatie verschilt.

Het statusoverzicht dat hier vroeger stond, is verhuisd naar
`ui/views/1_Status.py`.
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

import html
import json
import time
from collections.abc import Iterable
from typing import Any

import httpx
import streamlit as st

from proxy.audit import get_logs_by_session
from proxy.review import all_resolved
from proxy.templates import list_templates
from shared.config import settings
from shared.models import AuditEntry, Entity, PseudonymizationMode, Template
from ui.cookies import (
    MODE_EXTENDED,
    MODE_SIMPLIFIED,
    hydrate_testrun_mode,
    persist_testrun_mode,
)
from ui.navigation import REVIEW_QUEUE_PAGE
from ui.review_flow import (
    HOME_ACTION_ANCHOR_ID,
    SCROLL_TO_HOME_ACTION_KEY,
    mark_review_return_home,
)
from ui.testrun_helpers import (
    AnalysisResult,
    PrivacyReportContext,
    ResponseSignals,
    analyze_prompt,
    build_privacy_report_csv,
    build_privacy_report_md,
    entity_type_label,
    format_curl_equivalent,
    highlight_pairs,
    lay_explanation,
    pseudonymized_highlights,
    reconcile_analysis_for_display,
    response_signals,
    summarize_for_lay_user,
)
from ui.theme import (
    HIGHLIGHT_ONE_WAY,
    HIGHLIGHT_PENDING,
    HIGHLIGHT_TWO_WAY,
)
from ui.ui_extras import (
    attention_notice,
    scroll_to_element,
    section_heading,
    section_spacer,
)

# ---------------------------------------------------------------------------
# State-init: één canonieke set keys, gedeeld door beide modi.
# ---------------------------------------------------------------------------

_STATE_DEFAULTS: dict[str, Any] = {
    "testrun_mode": MODE_SIMPLIFIED,
    "testrun_dossier": "",
    "testrun_template_id": None,
    "testrun_analysis": None,
    "testrun_response": None,
    "testrun_session_id": None,
    "testrun_history": [],
    "_last_upload_id": "",
    "_review_redirect_target": "",
}

_DOSSIER_WIDGET_KEY = "home_dossier_text"
_LLM_RESPONSE_ANCHOR_ID = "pylades-llm-response"
_SCROLL_TO_LLM_RESPONSE_KEY = "_scroll_to_llm_response"
_SEND_CAPTION = (
    "Door op deze knop te klikken stuur je de gepseudonimiseerde "
    "versie naar het externe LLM."
)
_PENDING_DRY_RUN_KEY = "_pending_dry_run"


def _persist_dossier(text: str) -> None:
    """Canonical dossier-opslag (los van widget-key — die mag alleen vóór render)."""
    st.session_state.testrun_dossier = text


def _restore_dossier_from_response() -> None:
    """Herstel dossier na terugkeer van Review-queue als het widget-veld leeg is."""
    if st.session_state.testrun_dossier.strip():
        return
    rec = st.session_state.testrun_response
    if isinstance(rec, dict):
        saved = str(rec.get("dossier") or "")
        if saved:
            _persist_dossier(saved)


def _seed_dossier_widget() -> None:
    """Sync widget-key vanuit canonical state vóór render van het textarea."""
    canonical = st.session_state.testrun_dossier
    widget_val = str(st.session_state.get(_DOSSIER_WIDGET_KEY, ""))
    if _DOSSIER_WIDGET_KEY not in st.session_state or (
        not widget_val.strip() and canonical.strip()
    ):
        st.session_state[_DOSSIER_WIDGET_KEY] = canonical


def _show_inline_status(slot: Any, message: str) -> None:
    """Statusregel direct onder de actie-knoppen (i.p.v. spinner rechtsboven)."""
    slot.markdown(
        f'<p class="pylades-inline-status">'
        f'<span class="pylades-inline-status__dot"></span>'
        f"{html.escape(message)}</p>",
        unsafe_allow_html=True,
    )


def _template_select_index(templates: list[Template]) -> int:
    tid = st.session_state.testrun_template_id
    if tid is None:
        return 0
    for idx, tpl in enumerate(templates):
        if tpl.id == tid:
            return idx
    return 0


for _key, _default in _STATE_DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _default


def _reset_run_state() -> None:
    """Wis preview + response (na upload/template-wissel)."""
    st.session_state.testrun_analysis = None
    st.session_state.testrun_response = None
    st.session_state.testrun_session_id = None


# ---------------------------------------------------------------------------
# Highlight-styling — dark-theme-vriendelijk (semi-transparante overlays).
# ---------------------------------------------------------------------------

_HIGHLIGHT_STYLE: dict[str, str] = {
    "one_way": HIGHLIGHT_ONE_WAY,
    "two_way": HIGHLIGHT_TWO_WAY,
    "pending": HIGHLIGHT_PENDING,
}


def _highlight_text(text: str, highlights: Iterable[tuple[str, str]]) -> str:
    """Wrap voorkomens van elke needle in `<mark style="…">`, overlap-veilig.

    Strategie: sorteer needles op aflopende lengte zodat 'Jan Pietersen'
    voorrang krijgt op 'Pietersen' (een korte needle die binnen een al
    bezette span zou vallen, wordt overgeslagen). Daarna één keer linear
    door de tekst, escape-en + invoegen.
    """
    if not text:
        return ""
    sorted_h = sorted(
        ((needle, css_key) for needle, css_key in highlights if needle),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    occupied = bytearray(len(text))
    spans: list[tuple[int, int, str]] = []
    for needle, css_key in sorted_h:
        start = 0
        while True:
            idx = text.find(needle, start)
            if idx < 0:
                break
            end = idx + len(needle)
            if not any(occupied[idx:end]):
                spans.append((idx, end, css_key))
                for i in range(idx, end):
                    occupied[i] = 1
            start = idx + 1
    spans.sort()

    out: list[str] = []
    cursor = 0
    for s, e, css_key in spans:
        out.append(html.escape(text[cursor:s]))
        style = _HIGHLIGHT_STYLE.get(css_key, _HIGHLIGHT_STYLE["one_way"])
        out.append(f'<mark style="{style}">{html.escape(text[s:e])}</mark>')
        cursor = e
    out.append(html.escape(text[cursor:]))
    return "".join(out)


def _render_preview_block(text: str, highlights: Iterable[tuple[str, str]] = ()) -> None:
    """Toon een prompt-voorbeeld met regelafbreking i.p.v. horizontale scroll."""
    body = _highlight_text(text, highlights) if highlights else html.escape(text)
    st.markdown(
        '<pre style="white-space: pre-wrap; word-break: break-word; '
        "background: rgba(255,255,255,0.04); padding: 0.75rem; "
        "border-radius: 0.5rem; font-size: 0.85rem; line-height: 1.4; "
        'margin: 0 0 0.75rem 0;">'
        f"{body}</pre>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Proxy-call met latency-meting (gedeeld door Eenvoudig + Uitgebreid).
# ---------------------------------------------------------------------------


def _post_to_proxy(
    template_id: int,
    dossier_text: str,
    resume_session: str | None = None,
) -> dict[str, Any]:
    """Verstuur een echte request via de lokale proxy.

    Body-shape volgt PLAN §15a: `{template_id, dossier, resume_session}`.
    Retourneert een dict met `status`, `payload`, `session_id`, `latency_ms`,
    `dossier` (voor latere hervat) en `error` (bij netwerk-fout).
    """
    url = f"http://127.0.0.1:{settings.proxy_port}/v1/messages"
    body: dict[str, Any] = {
        "template_id": template_id,
        "dossier": dossier_text,
    }
    if resume_session:
        body["resume_session"] = resume_session
    headers = {"content-type": "application/json"}

    start = time.monotonic()
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return {
            "status": 0,
            "payload": {"error": str(exc)},
            "session_id": "",
            "latency_ms": int((time.monotonic() - start) * 1000),
            "dossier": dossier_text,
            "error": str(exc),
        }
    latency_ms = int((time.monotonic() - start) * 1000)
    session_id = response.headers.get("X-Pylades-Session", "")
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if not session_id and isinstance(payload, dict):
        session_id = str(payload.get("session_id", ""))
    return {
        "status": response.status_code,
        "payload": payload,
        "session_id": session_id,
        "latency_ms": latency_ms,
        "dossier": dossier_text,
        "error": None,
    }


def _store_response_in_history(record: dict[str, Any], template_id: int | None) -> None:
    """Bewaar een response-snapshot in de sessie-historie voor Uitgebreid."""
    history: list[dict[str, Any]] = st.session_state.testrun_history
    history.append(
        {
            "template_id": template_id,
            "status": record["status"],
            "session_id": record["session_id"],
            "latency_ms": record.get("latency_ms"),
            "is_resume": bool(record.get("resume_session")),
        }
    )
    # Cap op 20 om de UI niet te laten uitdijen.
    st.session_state.testrun_history = history[-20:]


# ---------------------------------------------------------------------------
# Hero + modus-schakelaar
# ---------------------------------------------------------------------------

st.title("Vertrouw je dossier toe aan Pylades")
st.markdown(
    "Kies een prompt, plak patiëntinformatie en zie wat het externe LLM "
    "zou zien voordat je verstuurt."
)

hydrate_testrun_mode(default=st.session_state.testrun_mode)

mode_choice = st.pills(
    "Modus",
    options=[MODE_SIMPLIFIED, MODE_EXTENDED],
    default=st.session_state.testrun_mode,
    selection_mode="single",
    key="testrun_mode_pills",
    label_visibility="collapsed",
    help=(
        "Vereenvoudigd: gewone taal, één doel per stap. "
        "Uitgebreid: alle diagnostics."
    ),
)
if mode_choice:
    st.session_state.testrun_mode = mode_choice
    persist_testrun_mode(mode_choice)
mode: str = st.session_state.testrun_mode
is_simple = mode == MODE_SIMPLIFIED


# ---------------------------------------------------------------------------
# Prompt-keuze
# ---------------------------------------------------------------------------

templates = list_templates()

if not templates:
    st.warning(
        "Geen prompts gevonden. Maak er eerst eentje aan via de pagina "
        "**Prompts** in het menu links."
    )
    st.stop()

_restore_dossier_from_response()


def _template_label(tpl: Template, *, include_id: bool) -> str:
    base = f"{tpl.groep} · {tpl.naam}"
    if include_id:
        return f"{base}  (id={tpl.id})"
    return base


_template_labels = [_template_label(t, include_id=not is_simple) for t in templates]

with st.container(border=not is_simple):
    if is_simple:
        section_heading("Prompt")
    else:
        st.markdown("**Prompt**")
    label = st.radio(
        "Kies een prompt",
        _template_labels,
        index=_template_select_index(templates),
        key="home-template-radio",
        label_visibility="collapsed",
    )
    template = templates[_template_labels.index(label)]
    if template.id != st.session_state.testrun_template_id:
        st.session_state.testrun_template_id = template.id
        _reset_run_state()
    if template.beschrijving:
        st.caption(template.beschrijving)
        st.markdown("")
    if not is_simple:
        st.caption(
            f"Provider/model: `{template.llm_provider}` / `{template.llm_naam}` · "
            f"max_tokens: `{template.max_tokens}` · "
            f"prompt-default modus: "
            f"`{template.default_mode.value if template.default_mode else '— (super-default)'}`"
        )
        with st.expander("Prompt-template (read-only)", expanded=False):
            st.code(template.prompt_tekst or "(leeg)", language="text")


# ---------------------------------------------------------------------------
# Dossier-veld + file-upload
# ---------------------------------------------------------------------------

with st.container(border=not is_simple):
    if is_simple:
        section_heading(
            "Patiëntdossier",
            caption="Het externe LLM ziet alleen de geanonimiseerde versie.",
        )
    else:
        st.markdown("**Patiëntdossier**")
    _seed_dossier_widget()
    dossier = st.text_area(
        "Plak hier je dossier. Het externe LLM ziet alleen de geanonimiseerde versie.",
        height=220,
        placeholder=(
            "Mevrouw Pietersen, BSN 123456782, woont op postcode 7411AB in "
            "Deventer. Ze is geboren op 03-04-1972."
        ),
        label_visibility="collapsed" if is_simple else "visible",
        key=_DOSSIER_WIDGET_KEY,
    )
    if dossier != st.session_state.testrun_dossier:
        st.session_state.testrun_dossier = dossier
        _reset_run_state()
    upload = st.file_uploader(
        "Of upload een .txt-dossier",
        type=["txt"],
        key="home-dossier-upload",
        label_visibility="collapsed",
    )
    if upload is not None:
        fid = f"{upload.name}:{upload.size}"
        if st.session_state._last_upload_id != fid:
            try:
                uploaded_text = upload.read().decode("utf-8", errors="replace")
            except OSError as exc:
                st.error(f"Kan upload niet lezen: {exc}")
            else:
                _persist_dossier(uploaded_text)
                st.session_state._last_upload_id = fid
                _reset_run_state()
                st.rerun()


# ---------------------------------------------------------------------------
# Knoppen — drie-staps-keten in Eenvoudig, split in Uitgebreid.
# ---------------------------------------------------------------------------


def _dossier_blocking_message() -> str | None:
    if not dossier.strip():
        return "Patiëntdossier is leeg."
    if not template.prompt_tekst or "{input}" not in template.prompt_tekst:
        return (
            "Prompt heeft geen geldige prompt-tekst met `{input}`. "
            "Pas hem aan via de pagina **Prompts**."
        )
    return None


def _do_dry_run() -> None:
    err = _dossier_blocking_message()
    if err:
        st.error(err)
        return
    st.session_state.testrun_analysis = analyze_prompt(template, dossier)
    st.session_state.testrun_response = None


def _do_real_post(
    *,
    resume_session: str | None = None,
    status_slot: Any | None = None,
    status_message: str = "Versturen naar extern LLM - dit kan even duren…",
) -> None:
    err = _dossier_blocking_message()
    if err:
        st.error(err)
        return
    if status_slot is not None:
        _show_inline_status(status_slot, status_message)
    record = _post_to_proxy(
        int(template.id or 0),
        dossier,
        resume_session=resume_session,
    )
    if status_slot is not None:
        status_slot.empty()
    record["resume_session"] = resume_session
    _persist_dossier(dossier)
    st.session_state.testrun_response = record
    if record["session_id"]:
        st.session_state.testrun_session_id = record["session_id"]
    if resume_session:
        signals = response_signals(record["status"], record["payload"])
        if signals.assistant_text:
            st.session_state[_SCROLL_TO_LLM_RESPONSE_KEY] = True
    _store_response_in_history(record, template.id)


def _navigate_to_review(session_id: str) -> None:
    _persist_dossier(dossier)
    mark_review_return_home()
    st.session_state["review_session_override"] = session_id
    st.query_params["session"] = session_id
    st.switch_page(REVIEW_QUEUE_PAGE)


analysis: AnalysisResult | None = st.session_state.testrun_analysis
session_id_state: str | None = st.session_state.testrun_session_id
response_record: dict[str, Any] | None = st.session_state.testrun_response
session_resolved = bool(session_id_state and all_resolved(session_id_state))
display_analysis: AnalysisResult | None = (
    reconcile_analysis_for_display(
        analysis,
        template,
        proxy_session_id=session_id_state,
        session_resolved=session_resolved,
        response_status=response_record.get("status") if response_record else None,
    )
    if analysis is not None
    else None
)

st.markdown(
    f'<div id="{HOME_ACTION_ANCHOR_ID}"></div>',
    unsafe_allow_html=True,
)

if is_simple:
    # --- Eenvoudig: drie-staps-keten, max één primaire knop tegelijk -----
    if analysis is None and not session_resolved:
        start_clicked = st.button(
            "Start",
            type="primary",
            use_container_width=False,
            help=(
                "Doet een voorbeeld-analyse (geen verbinding met het externe "
                "LLM, geen opslag). Daarna kies je bewust of je verstuurt."
            ),
        )
        start_status = st.empty()
        if st.session_state.pop(_PENDING_DRY_RUN_KEY, False):
            _show_inline_status(start_status, "Voorbeeld-analyse bezig…")
            _do_dry_run()
            st.rerun()
        if start_clicked:
            st.session_state[_PENDING_DRY_RUN_KEY] = True
            st.rerun()
    elif analysis is None and session_resolved:
        st.success(
            "Alle review-items zijn afgehandeld — je kunt nu hervatten naar "
            "het externe LLM."
        )
        hervat_clicked = st.button("Hervat naar extern LLM", type="primary")
        st.caption(_SEND_CAPTION)
        hervat_status = st.empty()
        if hervat_clicked:
            _do_real_post(
                resume_session=session_id_state,
                status_slot=hervat_status,
                status_message="Hervatten naar extern LLM - dit kan even duren…",
            )
            st.rerun()
    else:
        assert analysis is not None
        assert display_analysis is not None
        has_pending = bool(display_analysis.pending_review)
        if has_pending and not session_resolved:
            pending_count = len(display_analysis.pending_review)
            attention_notice(
                f"Pylades twijfelt over <strong>{pending_count}</strong> "
                "detectie(s). Beslis er eerst over voordat we het externe "
                "LLM iets laten zien."
            )
            review_clicked = st.button(
                "Open openstaande beslissingen",
                type="primary",
                help=(
                    "We plaatsen de twijfelgevallen in de review-queue (geen "
                    "verbinding met het externe LLM op dit moment) en "
                    "openen die pagina."
                ),
            )
            review_status = st.empty()
            if review_clicked:
                _show_inline_status(review_status, "Klaarzetten in de review-queue…")
                record = _post_to_proxy(int(template.id or 0), dossier)
                review_status.empty()
                if record["status"] == 423 and record["session_id"]:
                    st.session_state.testrun_session_id = record["session_id"]
                    st.session_state.testrun_response = record
                    _store_response_in_history(record, template.id)
                    _navigate_to_review(record["session_id"])
                else:
                    st.session_state.testrun_response = record
                    st.error(
                        "De proxy gaf een onverwachte status terug "
                        f"(HTTP {record['status']}). Open Uitgebreid voor "
                        "de details."
                    )
        else:
            resume = session_id_state if session_resolved else None
            button_label = (
                "Hervat naar extern LLM" if resume else "Verstuur naar extern LLM"
            )
            send_clicked = st.button(
                button_label,
                type="primary",
            )
            st.caption(_SEND_CAPTION)
            send_status = st.empty()
            if send_clicked:
                _do_real_post(
                    resume_session=resume,
                    status_slot=send_status,
                )
                st.rerun()

    if analysis is not None and st.button(
        "Begin opnieuw",
        type="secondary",
        help="Wist de analyse en eventueel antwoord. Het dossier blijft staan.",
    ):
        _reset_run_state()
        st.rerun()
else:
    action_status = st.empty()
    # --- Uitgebreid: huidige split (dry-run vs verstuur) ------------------
    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button("Analyseer anonimisatie", use_container_width=True):
            _do_dry_run()
            st.rerun()
    with col_b:
        can_send = bool(dossier.strip()) and template.id is not None
        resume_session = session_id_state if session_resolved else None
        send_label = "Hervat naar extern LLM" if resume_session else "Verstuur naar extern LLM"
        if st.button(
            send_label,
            use_container_width=True,
            type="primary",
            disabled=not can_send,
            help=(
                None
                if can_send
                else "Vul een patiëntdossier in en kies een geldige prompt."
            ),
        ):
            _do_real_post(
                resume_session=resume_session,
                status_slot=action_status,
            )
            st.rerun()
    if session_id_state and not all_resolved(session_id_state):
        st.warning(
            f"Sessie `{session_id_state}` heeft nog openstaande review-items. "
            "Los ze op via **Review-queue** voordat je hervat."
        )
        if st.button("Open review-queue voor deze sessie"):
            _navigate_to_review(session_id_state)

if st.session_state.pop(SCROLL_TO_HOME_ACTION_KEY, False):
    scroll_to_element(HOME_ACTION_ANCHOR_ID)


# ---------------------------------------------------------------------------
# Analyse-preview rendering
# ---------------------------------------------------------------------------


def _render_legend() -> None:
    parts = [
        ('<mark style="' + _HIGHLIGHT_STYLE["one_way"] + '">verborgen</mark>'),
        ('<mark style="' + _HIGHLIGHT_STYLE["two_way"] + '">verborgen + vertaald terug</mark>'),
        ('<mark style="' + _HIGHLIGHT_STYLE["pending"] + '">wacht op beoordeling</mark>'),
    ]
    st.markdown(
        '<div style="font-size: 0.8rem; opacity: 0.75; margin-bottom: 0.5rem;">'
        "Markering: " + " · ".join(parts) + "</div>",
        unsafe_allow_html=True,
    )


def _entity_panel_class(ent: Entity, *, pending: bool = False) -> str:
    if pending:
        return "pylades-soft-panel pylades-soft-panel--pending"
    if ent.effective_mode is PseudonymizationMode.TWO_WAY:
        return "pylades-soft-panel pylades-soft-panel--two-way"
    return "pylades-soft-panel pylades-soft-panel--one-way"


def _render_eenvoudig_entity_row(
    ent: Entity,
    *,
    pending: bool = False,
    show_pseudonym: bool = True,
) -> None:
    panel = _entity_panel_class(ent, pending=pending)
    pseudo_html = ""
    if show_pseudonym:
        pseudo = ent.pseudonym or "—"
        pseudo_html = (
            f'<div style="opacity:0.85; margin-top:0.25rem;">Wordt vervangen door: '
            f"<code>{html.escape(pseudo)}</code></div>"
        )
    st.markdown(
        f'<div class="{panel}">'
        f"<strong>{html.escape(ent.original)}</strong> "
        f"· <em>{html.escape(entity_type_label(ent))}</em>"
        f'<div style="opacity:0.75; font-size:0.85rem; margin-top:0.2rem;">'
        f"{html.escape(lay_explanation(ent, pending=pending))}</div>"
        f"{pseudo_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


def _accent_strip_class(
    result: AnalysisResult,
    response_status: int | None,
    *,
    session_resolved: bool = False,
) -> str:
    """Linkerrand-kleur voor de samenvattingsstrip op basis van status."""
    waiting = bool(result.pending_review) or (
        response_status == 423 and not session_resolved
    )
    if waiting:
        return "pylades-accent-strip pylades-accent-strip--attention"
    if response_status is not None and (
        response_status == 0 or (response_status >= 400 and response_status != 423)
    ):
        return "pylades-accent-strip pylades-accent-strip--error"
    return "pylades-accent-strip pylades-accent-strip--ok"


def _eenvoudig_analysis_caption(
    response_status: int | None,
    *,
    session_resolved: bool,
) -> str:
    if response_status == 200:
        return "Antwoord ontvangen van het externe LLM."
    if response_status == 423 and session_resolved:
        return "Review afgehandeld — je kunt nu naar het externe LLM versturen."
    if response_status == 423:
        return (
            "De proxy wacht op review-beslissingen voordat er iets naar het "
            "externe LLM gaat."
        )
    return (
        "Veilige voorbeeld-analyse — er is nog niets verstuurd naar het "
        "externe LLM en niets opgeslagen."
    )


def _render_eenvoudig_analysis(
    result: AnalysisResult,
    *,
    session_resolved: bool,
) -> None:
    response_status = response_record["status"] if response_record else None
    summary = summarize_for_lay_user(
        result,
        response_status=response_status,
        session_resolved=session_resolved,
    )
    strip_class = _accent_strip_class(
        result,
        response_status,
        session_resolved=session_resolved,
    )
    caption = _eenvoudig_analysis_caption(
        response_status,
        session_resolved=session_resolved,
    )
    st.markdown(
        f'<div class="{strip_class}">'
        f"<strong>{html.escape(summary.summary_line)}</strong>"
        f'<div style="opacity:0.75; font-size:0.85rem; margin-top:0.35rem;">'
        f"{html.escape(caption)}"
        "</div></div>",
        unsafe_allow_html=True,
    )

    section_heading("Wat het externe LLM zou zien")
    _render_preview_block(result.pseudonymized, pseudonymized_highlights(result))

    if result.entities:
        section_heading("Beschermde gegevens")
        for ent in result.entities:
            _render_eenvoudig_entity_row(ent)
    else:
        st.info(
            "Geen persoonsgegevens gedetecteerd — er is niets om te beschermen."
        )

    if result.pending_review:
        section_heading("Wacht op jouw beoordeling")
        for ent in result.pending_review:
            _render_eenvoudig_entity_row(ent, pending=True, show_pseudonym=False)


def _render_uitgebreid_analysis(result: AnalysisResult) -> None:
    originals_h, generalized_h, _ = highlight_pairs(result)
    _render_legend()

    cols = st.columns(2)
    with cols[0]:
        st.markdown("**Origineel (samengesteld)**")
        _render_preview_block(result.original, originals_h)
        if result.generalized != result.original:
            st.markdown("**Na generalisering**")
            _render_preview_block(result.generalized, generalized_h)
    with cols[1]:
        st.markdown("**Pseudonimized (zoals upstream LLM dit zou zien)**")
        _render_preview_block(result.pseudonymized, pseudonymized_highlights(result))

    st.markdown("**Mapping**")
    if not result.entities:
        st.info("Geen entiteiten gedetecteerd.")
    else:
        rows = [
            {
                "Origineel": ent.original,
                "Type": ent.entity_type.value,
                "Categorie": (ent.category.value if ent.category else ""),
                "Pseudoniem": ent.pseudonym or "",
                "Modus": _badge(ent.effective_mode),
                "Confidence": f"{ent.confidence:.2f}",
                "Laag": ent.detection_layer.value,
            }
            for ent in result.entities
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

    if result.pending_review:
        st.info(
            f"Bij **Verstuur naar extern LLM** zou de proxy "
            f"{len(result.pending_review)} entiteit(en) onder de confidence-"
            "threshold in de review-queue plaatsen (HTTP 423). De queue is nu "
            "nog leeg — `Analyseer anonimisatie` is een dry-run en schrijft "
            "niets weg. Klik **Verstuur** om de sessie aan te maken, los ze "
            "op via **Review-queue**, en klik daarna **Hervat naar extern LLM**."
        )


def _badge(mode: PseudonymizationMode | None) -> str:
    if mode is PseudonymizationMode.TWO_WAY:
        return "[2w]"
    if mode is PseudonymizationMode.ONE_WAY:
        return "[1w]"
    return "[?]"


# ---------------------------------------------------------------------------
# Response-rendering (status-strip + bubble + expander).
# ---------------------------------------------------------------------------


def _render_response_status_strip(
    signals: ResponseSignals,
    *,
    session_id: str | None = None,
) -> None:
    if signals.status == 423:
        if session_id and all_resolved(session_id):
            st.warning(
                "**HTTP 423** — er staan geen openstaande detectie-items meer "
                "in de **Review-queue** voor deze sessie. Hervat "
                "(via de knop boven) naar het externe LLM."
            )
        else:
            st.warning(
                "**HTTP 423** — de proxy heeft de detectie-items met lage "
                "confidence in de **Review-queue** geplaatst. Open die pagina "
                "(via de knop boven) en beslis per item."
            )
        return
    if signals.status == 0:
        st.error(
            "**Geen verbinding met de proxy** — staat `uv run python "
            "scripts/pylades_services.py status` op groen?"
        )
        return
    if signals.status >= 500:
        st.error(
            f"**HTTP {signals.status}** — fout bij het externe LLM."
        )
    elif signals.status >= 400:
        st.error(f"**HTTP {signals.status}** — verzoek geweigerd door de proxy.")
    elif signals.is_refusal:
        st.warning(
            "**Het externe LLM weigerde te antwoorden** (stop_reason="
            "`refusal`)."
        )
    elif signals.is_empty:
        st.warning(
            "**Antwoord ontvangen, maar leeg** — het externe LLM gaf geen "
            "tekst terug."
        )
    elif signals.is_truncated:
        st.warning(
            "**Antwoord is afgebroken** op `max_tokens`. Verhoog "
            "`max_tokens` in de prompt als je het hele antwoord wil zien."
        )
    else:
        st.success(f"**HTTP {signals.status}** — antwoord ontvangen.")
    if signals.error_message:
        st.caption(f"Bericht: {signals.error_message}")


def _render_eenvoudig_response(record: dict[str, Any]) -> None:
    signals = response_signals(record["status"], record["payload"])
    _render_response_status_strip(
        signals,
        session_id=record.get("session_id") or None,
    )
    if signals.status == 423:
        return
    if signals.assistant_text:
        st.markdown(
            f'<div id="{_LLM_RESPONSE_ANCHOR_ID}" tabindex="-1"></div>',
            unsafe_allow_html=True,
        )
        section_heading("Antwoord van het externe LLM")
        st.markdown(
            f'<div class="pylades-llm-response">'
            f"{html.escape(signals.assistant_text)}</div>",
            unsafe_allow_html=True,
        )
    with st.expander("Technische details", expanded=False):
        st.caption(
            f"Latency: {record.get('latency_ms', '—')} ms · "
            f"Sessie-id: `{record.get('session_id') or '—'}`"
        )
        st.json(record["payload"])


def _render_uitgebreid_response(record: dict[str, Any]) -> None:
    status = record["status"]
    sid = record["session_id"]
    if sid:
        st.caption(
            f"Session-id: `{sid}` — bekijk de audit-rij voor alle vier versies. "
            f"Latency: {record.get('latency_ms', '—')} ms"
        )
    signals = response_signals(status, record["payload"])
    _render_response_status_strip(
        signals,
        session_id=record.get("session_id") or None,
    )
    st.markdown("**Response (de-pseudonimized waar TWO_WAY van toepassing is)**")
    st.json(record["payload"])


# ---------------------------------------------------------------------------
# Uitgebreid extras: curl, raw upstream-body, sessie-historie.
# ---------------------------------------------------------------------------


def _render_uitgebreid_extras(result: AnalysisResult | None) -> None:
    if result is None and not response_record and not st.session_state.testrun_history:
        return
    with st.container(border=True):
        st.markdown("### Uitgebreid — diagnostiek")
        resume = (
            session_id_state
            if session_id_state and all_resolved(session_id_state)
            else None
        )
        curl_cmd = format_curl_equivalent(
            template_id=int(template.id or 0),
            dossier=dossier,
            proxy_port=settings.proxy_port,
            resume_session=resume,
        )
        st.markdown("**Curl-equivalent**")
        st.code(curl_cmd, language="bash")

        if result is not None:
            st.markdown("**Raw upstream-body (wat naar het externe LLM zou gaan)**")
            upstream = {
                "model": template.llm_naam,
                "max_tokens": template.max_tokens,
                "messages": [{"role": "user", "content": result.pseudonymized}],
            }
            st.code(json.dumps(upstream, ensure_ascii=False, indent=2), language="json")

        history = st.session_state.testrun_history
        if history:
            st.markdown("**Sessie-historie (deze Streamlit-sessie)**")
            rows = [
                {
                    "#": idx + 1,
                    "Status": item["status"],
                    "Sessie": (item["session_id"][:8] + "…")
                    if item["session_id"]
                    else "—",
                    "Latency (ms)": item.get("latency_ms") or "—",
                    "Hervat": "ja" if item.get("is_resume") else "nee",
                    "Prompt": item.get("template_id") or "—",
                }
                for idx, item in enumerate(history)
            ]
            st.dataframe(rows, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Privacy-rapport export (markdown + CSV, scope: laatste run / sessie).
# ---------------------------------------------------------------------------


def _build_privacy_context(scope: str) -> PrivacyReportContext:
    response_status = response_record["status"] if response_record else None
    stop_reason: str | None = None
    if response_record:
        signals = response_signals(response_record["status"], response_record["payload"])
        stop_reason = signals.stop_reason
    audit_entries: tuple[AuditEntry, ...] = ()
    if scope == "session" and session_id_state:
        audit_entries = tuple(get_logs_by_session(session_id_state))
    return PrivacyReportContext(
        template_naam=template.naam,
        template_groep=template.groep,
        session_id=session_id_state or (analysis.session_id if analysis else ""),
        response_status=response_status,
        response_stop_reason=stop_reason,
        audit_entries=audit_entries,
        two_way_justification=template.two_way_justification,
    )


def _render_privacy_report_export(result: AnalysisResult) -> None:
    with st.expander("Privacy-rapport (FG/DPO)", expanded=False):
        scope_label = st.radio(
            "Scope",
            ("Alleen laatste run", "Hele sessie"),
            horizontal=True,
            key="privacy-report-scope",
            help=(
                "Sessie-scope voegt audit-rijen toe (incl. hervat-cycli) en is "
                "alleen zinvol na een echte verstuur."
            ),
        )
        scope = "session" if scope_label == "Hele sessie" else "last_run"
        ctx = _build_privacy_context(scope)
        md = build_privacy_report_md(result, ctx)
        csv_text = build_privacy_report_csv(result, ctx)
        cols = st.columns(2)
        with cols[0]:
            st.download_button(
                "FG-rapport (markdown)",
                md,
                file_name=_report_filename(ctx, "md"),
                mime="text/markdown",
                use_container_width=True,
            )
        with cols[1]:
            st.download_button(
                "Entiteiten (CSV)",
                csv_text,
                file_name=_report_filename(ctx, "csv"),
                mime="text/csv",
                use_container_width=True,
            )


def _report_filename(ctx: PrivacyReportContext, ext: str) -> str:
    sid_part = ctx.session_id[:8] if ctx.session_id else "preview"
    return f"pylades-privacy-{sid_part}.{ext}"


# ---------------------------------------------------------------------------
# Resultaten-sectie
# ---------------------------------------------------------------------------

results_anchor = st.container(key="testrun-results")
with results_anchor:
    if not is_simple:
        section_spacer()

    if display_analysis is not None:
        if is_simple:
            _render_eenvoudig_analysis(
                display_analysis,
                session_resolved=session_resolved,
            )
        else:
            st.subheader("Analyse-preview (geen vault-writes)")
            _render_uitgebreid_analysis(display_analysis)

    if response_record is not None:
        if not is_simple:
            section_spacer()
        if is_simple:
            _render_eenvoudig_response(response_record)
            if st.session_state.pop(_SCROLL_TO_LLM_RESPONSE_KEY, False):
                scroll_to_element(_LLM_RESPONSE_ANCHOR_ID)
        else:
            st.subheader("Live response van de proxy")
            _render_uitgebreid_response(response_record)

    if not is_simple and display_analysis is not None:
        section_spacer()
        _render_uitgebreid_extras(display_analysis)

    if display_analysis is not None:
        if not is_simple:
            section_spacer()
        _render_privacy_report_export(display_analysis)
