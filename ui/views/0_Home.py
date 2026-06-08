"""Pylades — Home/Testrun-pagina.

Sinds v0.2.0 is de testrun-flow de homepagina: hij is voor alle doelgroepen
het belangrijkste interactiepunt. Een modus-schakelaar bovenaan
(Compact / Uitgebreid) bepaalt of we de plain-language-flow tonen
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
    ProgressStep,
    ResponseSignals,
    RunPhase,
    RunPhaseContext,
    StepStatus,
    accent_strip_class_for_run_phase,
    analysis_caption_for_run_phase,
    analyze_prompt_timed,
    build_privacy_report_csv,
    build_privacy_report_md,
    compute_run_phase,
    entity_type_label,
    external_llm_label,
    external_step_from_response,
    format_curl_equivalent,
    highlight_pairs,
    lay_explanation,
    progress_panel_html,
    progress_steps,
    pseudonymized_highlights,
    reconcile_analysis_for_display,
    response_signals,
    summarize_for_lay_user,
    with_external_step,
)
from ui.theme import (
    HIGHLIGHT_ONE_WAY,
    HIGHLIGHT_PENDING,
    HIGHLIGHT_TWO_WAY,
)
from ui.ui_extras import (
    attention_notice,
    render_llm_response_panel,
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
    "testrun_progress": None,
    "_pending_action": None,
    "_review_redirect_target": "",
}

_DOSSIER_WIDGET_KEY = "home_dossier_text"
_LLM_RESPONSE_ANCHOR_ID = "pylades-llm-response"
_PROGRESS_ANCHOR_ID = "pylades-progress-block"
_SCROLL_TO_LLM_RESPONSE_KEY = "_scroll_to_llm_response"
_SEND_CAPTION = (
    "Door op deze knop te klikken stuur je de gepseudonimiseerde "
    "versie naar het externe LLM."
)


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
    """Wis preview + response (na template-wissel of dossier-wijziging)."""
    st.session_state.testrun_analysis = None
    st.session_state.testrun_response = None
    st.session_state.testrun_session_id = None
    st.session_state.testrun_progress = None


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
    """Toon een opdracht-voorbeeld met regelafbreking i.p.v. horizontale scroll."""
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
# Proxy-call met latency-meting (gedeeld door Compact + Uitgebreid).
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
    "Kies een opdracht, plak patiëntinformatie en zie wat het externe LLM "
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
        "Compact: gewone taal, één doel per stap. "
        "Uitgebreid: alle diagnostics."
    ),
)
if mode_choice:
    st.session_state.testrun_mode = mode_choice
    persist_testrun_mode(mode_choice)
mode: str = st.session_state.testrun_mode
is_simple = mode == MODE_SIMPLIFIED


# ---------------------------------------------------------------------------
# Opdracht-keuze
# ---------------------------------------------------------------------------

templates = list_templates()

if not templates:
    st.warning(
        "Geen opdrachten gevonden. Maak er eerst eentje aan via de pagina "
        "**Opdrachten** in het menu links."
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
        section_heading("Opdracht")
    else:
        st.markdown("**Opdracht**")
    label = st.radio(
        "Kies een opdracht",
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
            f"opdracht-default modus: "
            f"`{template.default_mode.value if template.default_mode else '— (super-default)'}`"
        )
        with st.expander("Opdracht-template (read-only)", expanded=False):
            st.code(template.prompt_tekst or "(leeg)", language="text")


# ---------------------------------------------------------------------------
# Dossier-veld
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


# ---------------------------------------------------------------------------
# Knoppen — drie-staps-keten in Compact, split in Uitgebreid.
# ---------------------------------------------------------------------------


def _dossier_blocking_message() -> str | None:
    if not dossier.strip():
        return "Patiëntdossier is leeg."
    if not template.prompt_tekst or "{input}" not in template.prompt_tekst:
        return (
            "Opdracht heeft geen geldige opdrachttekst met `{input}`. "
            "Pas hem aan via de pagina **Opdrachten**."
        )
    return None


def _render_progress(progress_slot: Any, steps: list[ProgressStep]) -> None:
    """Schrijf het voortgangsblok in de vaste placeholder (één blok, zelfde plek)."""
    progress_slot.markdown(progress_panel_html(steps), unsafe_allow_html=True)


def _run_dry_run(progress_slot: Any) -> None:
    """Voer de voorbeeld-analyse uit en update het voortgangsblok live per laag."""
    err = _dossier_blocking_message()
    if err:
        st.error(err)
        return

    def _on_layer(timings: list[Any]) -> None:
        _render_progress(progress_slot, progress_steps(timings, template))

    result, timings = analyze_prompt_timed(template, dossier, on_layer=_on_layer)
    st.session_state.testrun_analysis = result
    st.session_state.testrun_response = None
    steps = progress_steps(timings, template)
    st.session_state.testrun_progress = steps
    _render_progress(progress_slot, steps)


def _run_send(
    progress_slot: Any,
    *,
    resume_session: str | None = None,
) -> None:
    """Verstuur naar het externe LLM en werk de externe-LLM-stap live bij."""
    err = _dossier_blocking_message()
    if err:
        st.error(err)
        return

    base_steps = st.session_state.testrun_progress or progress_steps([], template)
    running = ProgressStep(
        external_llm_label(template),
        StepStatus.RUNNING,
        note="bezig… (inclusief stap 1 t/m 3)",
    )
    _render_progress(progress_slot, with_external_step(base_steps, running))

    record = _post_to_proxy(
        int(template.id or 0),
        dossier,
        resume_session=resume_session,
    )
    record["resume_session"] = resume_session
    _persist_dossier(dossier)
    st.session_state.testrun_response = record
    if record["session_id"]:
        st.session_state.testrun_session_id = record["session_id"]
    signals = response_signals(record["status"], record["payload"])
    if signals.assistant_text:
        st.session_state[_SCROLL_TO_LLM_RESPONSE_KEY] = True
    _store_response_in_history(record, template.id)

    external = external_step_from_response(
        status=record["status"],
        latency_ms=record.get("latency_ms"),
        signals=signals,
        template=template,
    )
    final_steps = with_external_step(base_steps, external)
    st.session_state.testrun_progress = final_steps
    _render_progress(progress_slot, final_steps)


def _navigate_to_review(session_id: str) -> None:
    _persist_dossier(dossier)
    mark_review_return_home()
    st.session_state["review_session_override"] = session_id
    st.query_params["session"] = session_id
    st.switch_page(REVIEW_QUEUE_PAGE)


def _render_resume_ready_banner(result: AnalysisResult | None) -> None:
    """Bevestig dat de review klaar is en dat alleen hervatten nog rest."""
    protected = len(result.entities) if result else 0
    if protected == 1:
        recap = "1 gegeven blijft beschermd. "
    elif protected > 1:
        recap = f"{protected} gegevens blijven beschermd. "
    else:
        recap = ""
    st.markdown(
        '<div class="pylades-accent-strip pylades-accent-strip--ok">'
        "<strong>Alle twijfelgevallen zijn afgehandeld.</strong>"
        '<div style="opacity:0.75; font-size:0.85rem; margin-top:0.35rem;">'
        f"{html.escape(recap)}Alleen nog hervatten: de gepseudonimiseerde "
        "versie gaat dan naar het externe LLM."
        "</div></div>",
        unsafe_allow_html=True,
    )


def _render_complete_banner() -> None:
    """Bevestig succesvolle upstream-call — geen verstuur-knop meer nodig."""
    st.markdown(
        '<div class="pylades-accent-strip pylades-accent-strip--ok">'
        "<strong>Verstuurd — antwoord staat hieronder.</strong>"
        '<div style="opacity:0.75; font-size:0.85rem; margin-top:0.35rem;">'
        "Je kunt het antwoord van het externe LLM verderop op deze pagina lezen."
        "</div></div>",
        unsafe_allow_html=True,
    )


def _render_failed_banner(ctx: RunPhaseContext) -> None:
    """Fout na echte proxy-call — retry via Opnieuw proberen, niet Hervat."""
    detail = ""
    if ctx.response_signals and ctx.response_signals.error_message:
        detail = ctx.response_signals.error_message
    elif ctx.response_status == 0:
        detail = "Geen verbinding met de proxy."
    st.markdown(
        '<div class="pylades-accent-strip pylades-accent-strip--error">'
        "<strong>Versturen mislukt — probeer opnieuw.</strong>"
        + (
            f'<div style="opacity:0.75; font-size:0.85rem; margin-top:0.35rem;">'
            f"{html.escape(detail)}</div>"
            if detail
            else ""
        )
        + "</div>",
        unsafe_allow_html=True,
    )


def _render_action_banners(ctx: RunPhaseContext, result: AnalysisResult | None) -> None:
    if ctx.show_complete_banner:
        _render_complete_banner()
    elif ctx.show_failed_banner:
        _render_failed_banner(ctx)
    elif ctx.show_resume_ready_banner:
        _render_resume_ready_banner(result)


def _queue_dry_run() -> None:
    st.session_state["_pending_action"] = {"kind": "dry_run"}
    st.rerun()


def _queue_send(ctx: RunPhaseContext) -> None:
    st.session_state["_pending_action"] = {
        "kind": "send",
        "resume_session": ctx.resume_session,
    }
    st.rerun()


def _queue_review() -> None:
    st.session_state["_pending_action"] = {"kind": "review"}
    st.rerun()


def _run_review(progress_slot: Any) -> None:
    """Zet de twijfelgevallen in de review-queue; toont stap 4 live als ``bezig…``.

    De proxy geeft hier HTTP 423 terug (geen externe-LLM-call); bij succes
    navigeren we naar de Review-queue, bij een onverwachte status blijft de
    gebruiker op Home. In beide gevallen weerspiegelt stap 4 de uitkomst.
    """
    err = _dossier_blocking_message()
    if err:
        st.error(err)
        return

    base_steps = st.session_state.testrun_progress or progress_steps([], template)
    running = ProgressStep(
        external_llm_label(template),
        StepStatus.RUNNING,
        note=(
            "nog niet aangeroepen — twijfelgevallen klaarzetten voor jouw "
            "beoordeling (inclusief stap 1 t/m 3)…"
        ),
    )
    _render_progress(progress_slot, with_external_step(base_steps, running))

    record = _post_to_proxy(int(template.id or 0), dossier)
    st.session_state.testrun_response = record
    _store_response_in_history(record, template.id)

    external = external_step_from_response(
        status=record["status"],
        latency_ms=record.get("latency_ms"),
        signals=response_signals(record["status"], record["payload"]),
        template=template,
    )
    final_steps = with_external_step(base_steps, external)
    st.session_state.testrun_progress = final_steps
    _render_progress(progress_slot, final_steps)

    if record["status"] == 423 and record["session_id"]:
        st.session_state.testrun_session_id = record["session_id"]
        _navigate_to_review(record["session_id"])
    # Bij een onverwachte status blijven we op Home; de status-strip en het
    # voortgangsblok (stap 4) tonen de fout na de rerun.


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
run_ctx = compute_run_phase(
    analysis=analysis,
    display_analysis=display_analysis,
    session_id=session_id_state,
    session_resolved=session_resolved,
    response_record=response_record,
)

st.markdown(
    f'<div id="{HOME_ACTION_ANCHOR_ID}" style="scroll-margin-top: 6rem;"></div>',
    unsafe_allow_html=True,
)

# Zodra een actie in de wachtrij staat (klik → rerun → verwerking hieronder),
# disablen we de start-knoppen zodat verwerking niet dubbel kan worden gestart.
_processing = bool(st.session_state.get("_pending_action"))

if is_simple:
    # --- Compact: één primaire actie per run-fase -----------------------------
    if run_ctx.phase == RunPhase.IDLE:
        if st.button(
            "Start",
            type="primary",
            use_container_width=False,
            disabled=_processing,
            help=(
                "Doet een voorbeeld-analyse (geen verbinding met het externe "
                "LLM, geen opslag). Daarna kies je bewust of je verstuurt."
            ),
        ):
            _queue_dry_run()
    elif run_ctx.phase == RunPhase.REVIEW_PENDING:
        if display_analysis and display_analysis.pending_review:
            pending_count = len(display_analysis.pending_review)
            attention_notice(
                f"Pylades twijfelt over <strong>{pending_count}</strong> "
                "detectie(s). Beslis er eerst over voordat we het externe "
                "LLM iets laten zien."
            )
        else:
            attention_notice(
                "Er staan review-beslissingen open. Los ze op via de "
                "<strong>Review-queue</strong> voordat je verstuurt."
            )
        if st.button(
            "Open openstaande beslissingen",
            type="primary",
            disabled=_processing,
            help=(
                "We plaatsen de twijfelgevallen in de review-queue (geen "
                "verbinding met het externe LLM op dit moment) en "
                "openen die pagina."
            ),
        ):
            _queue_review()
    else:
        _render_action_banners(run_ctx, display_analysis)
        if run_ctx.send_button_label:
            send_clicked = st.button(
                run_ctx.send_button_label,
                type="primary",
                disabled=_processing,
            )
            if run_ctx.phase == RunPhase.READY_TO_SEND:
                st.caption(_SEND_CAPTION)
            if send_clicked:
                _queue_send(run_ctx)

    if analysis is not None and run_ctx.phase != RunPhase.IDLE and st.button(
        "Begin opnieuw",
        type="tertiary",
        help=(
            "Wist de analyse en het eventuele antwoord en sluit een "
            "afgehandelde sessie af. Je dossier blijft staan."
        ),
    ):
        _reset_run_state()
        st.rerun()
else:
    # --- Uitgebreid: huidige split (dry-run vs verstuur) ------------------
    col_a, col_b = st.columns([1, 1])
    with col_a:
        if st.button(
            "Analyseer anonimisatie",
            use_container_width=True,
            disabled=_processing,
        ):
            _queue_dry_run()
    with col_b:
        can_send = bool(dossier.strip()) and template.id is not None
        send_disabled = (
            _processing
            or not can_send
            or run_ctx.phase == RunPhase.REVIEW_PENDING
            or run_ctx.phase == RunPhase.COMPLETE
            or run_ctx.send_button_label is None
        )
        if run_ctx.send_button_label:
            if st.button(
                run_ctx.send_button_label,
                use_container_width=True,
                type="primary",
                disabled=send_disabled,
                help=(
                    None
                    if can_send and not send_disabled
                    else "Vul een patiëntdossier in en kies een geldige opdracht."
                    if not can_send
                    else "Actie niet beschikbaar in deze fase."
                ),
            ):
                _queue_send(run_ctx)
    if run_ctx.phase in (RunPhase.COMPLETE, RunPhase.FAILED):
        _render_action_banners(run_ctx, display_analysis)
    elif run_ctx.show_resume_ready_banner:
        _render_action_banners(run_ctx, display_analysis)
    if session_id_state and not all_resolved(session_id_state):
        st.warning(
            f"Sessie `{session_id_state}` heeft nog openstaande review-items. "
            "Los ze op via **Review-queue** voordat je hervat."
        )
        if st.button("Open review-queue voor deze sessie"):
            _navigate_to_review(session_id_state)

# ---------------------------------------------------------------------------
# Voortgangsindicator — één blok, vaste plek, altijd actueel zichtbaar
# ---------------------------------------------------------------------------
st.markdown(
    f'<div id="{_PROGRESS_ANCHOR_ID}" style="scroll-margin-top: 6rem;"></div>',
    unsafe_allow_html=True,
)
progress_slot = st.empty()
_pending_action = st.session_state.pop("_pending_action", None)
if _pending_action and _pending_action.get("kind") in {"dry_run", "send", "review"}:
    # Breng het voortgangsblok in beeld zodra verwerking start, zodat de
    # live statuswijzigingen (stap 1 t/m 4) altijd zichtbaar zijn.
    scroll_to_element(_PROGRESS_ANCHOR_ID)
if _pending_action and _pending_action.get("kind") == "dry_run":
    _run_dry_run(progress_slot)
    st.rerun()
elif _pending_action and _pending_action.get("kind") == "send":
    _run_send(progress_slot, resume_session=_pending_action.get("resume_session"))
    st.rerun()
elif _pending_action and _pending_action.get("kind") == "review":
    # `_run_review` navigeert bij succes (423) weg; bij een fout valt de uitkomst
    # af te lezen uit het voortgangsblok (stap 4) en de status-strip hieronder.
    _run_review(progress_slot)
    st.rerun()
elif st.session_state.testrun_progress:
    _render_progress(progress_slot, st.session_state.testrun_progress)

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
    run_ctx: RunPhaseContext,
) -> str:
    """Linkerrand-kleur voor de samenvattingsstrip — zelfde fase als actieknoppen."""
    _ = result
    return accent_strip_class_for_run_phase(run_ctx.phase)


def _eenvoudig_analysis_caption(run_ctx: RunPhaseContext) -> str:
    return analysis_caption_for_run_phase(run_ctx)


def _render_eenvoudig_analysis(
    result: AnalysisResult,
    *,
    run_ctx: RunPhaseContext,
    answer_text: str | None = None,
) -> None:
    response_status = response_record["status"] if response_record else None
    summary = summarize_for_lay_user(
        result,
        response_status=response_status,
        session_resolved=session_resolved,
        run_phase=run_ctx.phase,
    )
    strip_class = _accent_strip_class(result, run_ctx)
    caption = _eenvoudig_analysis_caption(run_ctx)
    st.markdown(
        f'<div class="{strip_class}">'
        f"<strong>{html.escape(summary.summary_line)}</strong>"
        f'<div style="opacity:0.75; font-size:0.85rem; margin-top:0.35rem;">'
        f"{html.escape(caption)}"
        "</div></div>",
        unsafe_allow_html=True,
    )

    # Antwoord van het externe LLM direct onder de samenvattingsstrip, met
    # extra accent voor attentiewaarde.
    if answer_text:
        render_llm_response_panel(
            answer_text,
            anchor_id=_LLM_RESPONSE_ANCHOR_ID,
            accent=True,
        )

    seen = run_ctx.phase == RunPhase.COMPLETE
    preview_heading = (
        "Wat het externe LLM heeft gezien"
        if seen
        else "Wat het externe LLM zou zien"
    )
    section_heading(preview_heading)
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
    run_ctx: RunPhaseContext | None = None,
) -> None:
    if signals.status == 423:
        if run_ctx and run_ctx.phase == RunPhase.READY_TO_SEND:
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
            "`max_tokens` in de opdracht als je het hele antwoord wil zien."
        )
    else:
        st.success(f"**HTTP {signals.status}** — antwoord ontvangen.")
    if signals.error_message:
        st.caption(f"Bericht: {signals.error_message}")


def _render_eenvoudig_response(
    record: dict[str, Any],
    *,
    show_answer_panel: bool = True,
) -> None:
    signals = response_signals(record["status"], record["payload"])
    _render_response_status_strip(
        signals,
        session_id=record.get("session_id") or None,
        run_ctx=run_ctx,
    )
    if signals.status == 423:
        return
    if show_answer_panel and signals.assistant_text:
        render_llm_response_panel(
            signals.assistant_text,
            anchor_id=_LLM_RESPONSE_ANCHOR_ID,
            accent=True,
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
        run_ctx=run_ctx,
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
        resume = run_ctx.resume_session
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
                    "Opdracht": item.get("template_id") or "—",
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

    # Antwoord van het externe LLM komt (compact) direct onder de
    # samenvattingsstrip in de analyse-render; in dat geval slaan we het
    # antwoordblok in de response-sectie over om dubbele weergave te voorkomen.
    _eenvoudig_answer_text: str | None = None
    if is_simple and response_record is not None:
        _resp_signals = response_signals(
            response_record["status"], response_record["payload"]
        )
        if _resp_signals.status != 423 and _resp_signals.assistant_text:
            _eenvoudig_answer_text = _resp_signals.assistant_text
    _answer_moved = _eenvoudig_answer_text is not None and display_analysis is not None

    if display_analysis is not None:
        if is_simple:
            _render_eenvoudig_analysis(
                display_analysis,
                run_ctx=run_ctx,
                answer_text=_eenvoudig_answer_text if _answer_moved else None,
            )
        else:
            st.subheader("Analyse-preview (geen vault-writes)")
            _render_uitgebreid_analysis(display_analysis)

    if response_record is not None:
        if not is_simple:
            section_spacer()
        if is_simple:
            _render_eenvoudig_response(
                response_record,
                show_answer_panel=not _answer_moved,
            )
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
