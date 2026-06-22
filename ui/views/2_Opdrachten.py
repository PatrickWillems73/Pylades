"""Pylades — Opdrachten-pagina.

CRUD over `templates`-tabel + per-EntityType pseudonimiseringsmodus
(BR-C06). De inhoudelijke logica zit in `proxy/templates.py` en
`proxy/pseudonymization.py`; deze pagina is alleen rendering en
form-validatie. Drie-koloms-tabel toont *resulterende* modus live zodat
de gebruiker nooit mentaal de drie-laagse resolver hoeft uit te voeren.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

# `streamlit run` zet alleen de script-directory op sys.path; zonder shim is
# `ui.*` of `shared.*` niet importeerbaar vanuit deze entry-pagina.
for _root in Path(__file__).resolve().parents:
    if (_root / "pyproject.toml").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

from typing import Any

import streamlit as st
from pydantic import ValidationError

import proxy.templates as _proxy_templates

importlib.reload(_proxy_templates)

from proxy.pseudonymization import (
    get_super_default_pseudonymization_mode,
    resolve_effective_mode_with_source,
)
from proxy.templates import (
    delete_template,
    get_template,
    list_templates,
    move_template,
    upsert_template,
)
from shared.models import EntityType, PseudonymizationMode, Template

st.title("Opdrachten")

_OVERRIDE_OPTIONS = (
    "Gebruik opdracht-default",
    "One-way",
    "Two-way",
)
_TEMPLATE_DEFAULT_OPTIONS = (
    "Gebruik super-default",
    "One-way",
    "Two-way",
)


def _option_to_mode(label: str) -> PseudonymizationMode | None:
    if label == "One-way":
        return PseudonymizationMode.ONE_WAY
    if label == "Two-way":
        return PseudonymizationMode.TWO_WAY
    return None


def _mode_to_option(mode: PseudonymizationMode | None) -> str:
    if mode is PseudonymizationMode.ONE_WAY:
        return "One-way"
    if mode is PseudonymizationMode.TWO_WAY:
        return "Two-way"
    return _OVERRIDE_OPTIONS[0]


def _mode_to_template_default_option(mode: PseudonymizationMode | None) -> str:
    if mode is PseudonymizationMode.ONE_WAY:
        return "One-way"
    if mode is PseudonymizationMode.TWO_WAY:
        return "Two-way"
    return _TEMPLATE_DEFAULT_OPTIONS[0]


def _has_any_two_way(
    template_default: PseudonymizationMode | None,
    overrides: dict[EntityType, PseudonymizationMode],
) -> bool:
    if template_default is PseudonymizationMode.TWO_WAY:
        return True
    return any(mode is PseudonymizationMode.TWO_WAY for mode in overrides.values())


def _render_overview() -> None:
    templates = list_templates()
    if not templates:
        st.info("Nog geen opdrachten. Maak er een aan in het tabblad Bewerken.")
        return
    st.caption(
        f"{len(templates)} opdracht(en) opgeslagen. "
        "Volgorde bepaalt de weergave op Home."
    )
    last_idx = len(templates) - 1
    for idx, tpl in enumerate(templates):
        with st.container(border=True):
            cols = st.columns([5, 2])
            with cols[0]:
                st.markdown(f"**{idx + 1}. {tpl.naam}** — _{tpl.groep}_")
                st.caption(tpl.beschrijving or "—")
                st.code(
                    f"provider={tpl.llm_provider} | model={tpl.llm_naam} | "
                    f"max_tokens={tpl.max_tokens} | "
                    f"laag3={'aan' if tpl.use_llm else 'uit'} | "
                    f"default_mode={tpl.default_mode.value if tpl.default_mode else '—'} | "
                    f"overrides={len(tpl.mode_overrides)}"
                )
            with cols[1]:
                up_col, down_col, del_col = st.columns(3)
                with up_col:
                    if st.button(
                        "↑",
                        key=f"up-{tpl.id}",
                        disabled=idx == 0,
                        use_container_width=True,
                    ):
                        move_template(int(tpl.id or 0), -1)
                        st.rerun()
                with down_col:
                    if st.button(
                        "↓",
                        key=f"down-{tpl.id}",
                        disabled=idx == last_idx,
                        use_container_width=True,
                    ):
                        move_template(int(tpl.id or 0), 1)
                        st.rerun()
                with del_col:
                    if st.button("Verwijder", key=f"del-{tpl.id}", use_container_width=True):
                        delete_template(int(tpl.id or 0))
                        st.rerun()


def _render_mode_table(
    template_default: PseudonymizationMode | None,
    overrides_state: dict[str, str],
) -> dict[EntityType, PseudonymizationMode]:
    """Tabel met EntityType / Modus-override / Resulterende modus.

    Returnt het up-to-date overrides-dict op basis van `overrides_state`,
    dat door Streamlit's form-state per render is bijgewerkt.
    """
    super_default = get_super_default_pseudonymization_mode()
    fake_template = Template(
        groep="x",
        naam="x",
        llm_provider="anthropic",
        llm_naam="x",
        default_mode=template_default,
        mode_overrides={},
        two_way_justification="dummy — alleen om validator te omzeilen voor preview",
        # `two_way_justification` is alleen vereist bij actieve TWO_WAY;
        # we voegen een stub toe zodat de preview-template ook met
        # template_default=TWO_WAY instantieert.
    )

    overrides: dict[EntityType, PseudonymizationMode] = {}
    header = st.columns([2, 2, 3])
    header[0].markdown("**EntityType**")
    header[1].markdown("**Modus-override**")
    header[2].markdown("**Resulterende modus**")

    for entity_type in EntityType:
        row = st.columns([2, 2, 3])
        with row[0]:
            st.code(entity_type.value)
        with row[1]:
            current_label = overrides_state.get(entity_type.value, _OVERRIDE_OPTIONS[0])
            selected = st.selectbox(
                f"override-{entity_type.value}",
                _OVERRIDE_OPTIONS,
                index=_OVERRIDE_OPTIONS.index(current_label),
                key=f"override-{entity_type.value}",
                label_visibility="collapsed",
            )
        override_mode = _option_to_mode(selected)
        if override_mode is not None:
            overrides[entity_type] = override_mode
            merged = {**fake_template.mode_overrides, entity_type: override_mode}
            fake_template = fake_template.model_copy(update={"mode_overrides": merged})

        effective, source = resolve_effective_mode_with_source(
            fake_template, entity_type, super_default
        )
        with row[2]:
            st.markdown(f"`{effective.value}` ({source})")

    return overrides


def _sync_mode_widget_state(selected_id: int | None, existing: Template | None) -> None:
    """Reset de modus-widget-keys zodra de gekozen opdracht wisselt.

    Streamlit's widget-protocol geeft `st.session_state[key]` voorrang op
    een `index=` of `value=`-parameter. Zonder deze synchronisatie blijven
    eerdere keuzes (van + Nieuwe opdracht of een ander #id) plakken in de
    selectboxen voor Opdracht-default en per-EntityType-overrides, en
    matchen die niet meer met wat in de DB voor deze opdracht staat.
    We detecteren een wisseling via een eigen `_form_last_id`-sentinel.
    """
    sentinel_key = "_template_form_last_id"
    sentinel = st.session_state.get(sentinel_key, "__unset__")
    if sentinel == selected_id:
        return
    st.session_state[sentinel_key] = selected_id
    st.session_state["template-default"] = _mode_to_template_default_option(
        existing.default_mode if existing else None
    )
    for entity_type in EntityType:
        st.session_state[f"override-{entity_type.value}"] = _mode_to_option(
            existing.mode_overrides.get(entity_type) if existing else None
        )


def _render_editor() -> None:
    templates = list_templates()
    choices = {"+ Nieuwe opdracht": None} | {
        f"#{t.id} — {t.naam} ({t.groep})": int(t.id or 0) for t in templates
    }
    selected_label = st.selectbox("Kies opdracht", list(choices.keys()))
    selected_id = choices[selected_label]
    existing = get_template(selected_id) if selected_id is not None else None

    _sync_mode_widget_state(selected_id, existing)

    with st.form("template-form", clear_on_submit=False):
        groep = st.text_input("Groep", value=existing.groep if existing else "")
        naam = st.text_input("Naam", value=existing.naam if existing else "")
        beschrijving = st.text_area("Beschrijving", value=existing.beschrijving if existing else "")
        llm_provider = st.text_input(
            "LLM provider", value=(existing.llm_provider if existing else "anthropic")
        )
        llm_naam = st.text_input(
            "LLM model", value=(existing.llm_naam if existing else "claude-opus-4-8")
        )
        prompt_tekst = st.text_area(
            "Opdracht-template",
            value=(existing.prompt_tekst if existing else ""),
            height=160,
            help=(
                "Bevat de LLM-opdracht plus exact één `{input}`-placeholder. "
                "Op de Home-pagina wordt het patiëntdossier daar ingevuld."
            ),
            placeholder=(
                "Vat het volgende patiëntdossier samen in maximaal 5 bullets.\n"
                "Markeer rode vlaggen.\n\n"
                "Dossier:\n{input}"
            ),
        )
        max_tokens = st.number_input(
            "Max tokens (responselengte vanuit het LLM)",
            min_value=1,
            max_value=200_000,
            value=int(existing.max_tokens if existing else 16_000),
            step=64,
            help="Wordt server-side aan het LLM meegegeven; clients kunnen dit niet overrulen.",
        )
        use_llm = st.toggle(
            "Laag 3 (lokaal Ollama-LLM) inschakelen",
            value=bool(existing.use_llm) if existing else False,
            help=(
                "Optioneel/eval: derde detectielaag via lokaal Ollama (product/"
                "project; NAME experimenteel). Normale NAME-detectie gebruikt "
                "regex + DEDUCE + rol-heuristiek zonder Ollama. Vereist een "
                "draaiende Ollama-server; bij falen soft terug op regex + DEDUCE. "
                "Default uit — zie TESTPLAN.md §8."
            ),
        )

        st.markdown("### Pseudonimiseringsmodus")
        st.caption(
            "Drie-laagse resolver: super-default → opdracht-default → per-EntityType override. "
            "De derde kolom toont resultaat live."
        )

        current_default_label = _mode_to_template_default_option(
            existing.default_mode if existing else None
        )
        default_label = st.selectbox(
            "Opdracht-default",
            _TEMPLATE_DEFAULT_OPTIONS,
            index=_TEMPLATE_DEFAULT_OPTIONS.index(current_default_label),
            key="template-default",
        )
        template_default = _option_to_mode(default_label)

        overrides_state = {
            entity_type.value: _mode_to_option(
                existing.mode_overrides.get(entity_type) if existing else None
            )
            for entity_type in EntityType
        }
        overrides = _render_mode_table(template_default, overrides_state)

        two_way_required = _has_any_two_way(template_default, overrides)
        two_way_justification = st.text_area(
            "Onderbouwing TWO_WAY (verplicht bij elke actieve TWO_WAY)",
            value=(existing.two_way_justification if existing else "") or "",
            help=(
                "BR-C06 vereist een gedocumenteerde functionele reden zodra "
                "ergens TWO_WAY actief is."
            ),
            placeholder="Bv. case-study analyse vereist herleidbaarheid van namen.",
        )

        submit = st.form_submit_button("Opslaan")
        if not submit:
            return

        try:
            template = Template(
                id=selected_id,
                groep=groep,
                naam=naam,
                beschrijving=beschrijving,
                llm_provider=llm_provider,
                llm_naam=llm_naam,
                prompt_tekst=prompt_tekst,
                max_tokens=int(max_tokens),
                use_llm=bool(use_llm),
                default_mode=template_default,
                mode_overrides=overrides,
                two_way_justification=(two_way_justification or None),
            )
        except ValidationError as exc:
            _render_validation_error(exc, two_way_required)
            return

        new_id = upsert_template(template)
        st.success(
            f"Opdracht **#{new_id}** opgeslagen "
            f"({'aangepast' if selected_id is not None else 'nieuw'})."
        )
        st.rerun()


def _render_validation_error(exc: ValidationError, two_way_required: bool) -> None:
    if two_way_required:
        st.error("BR-C06: zodra ergens TWO_WAY actief is, is een onderbouwing verplicht.")
    else:
        st.error("Opdracht-validatie faalde:")
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []))
        msg: Any = err.get("msg", "")
        st.write(f"- `{loc}`: {msg}")


tab1, tab2 = st.tabs(["Overzicht", "Bewerken"])
with tab1:
    _render_overview()
with tab2:
    _render_editor()
