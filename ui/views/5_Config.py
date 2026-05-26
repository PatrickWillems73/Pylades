"""Pylades — Config-pagina.

Threshold-sliders per detectielaag, generalisering-toggles, super-default
pseudonimiseringsmodus en sleutelrotatie-flow. De inhoudelijke logica
(CSV-export, key-rotatie) leeft in `proxy/mapping.py` en `shared/crypto.py`
zodat tests deze acties kunnen valideren zonder Streamlit-context.
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

from proxy.mapping import export_mappings_csv
from shared.config import settings
from shared.crypto import rotate_global_secret
from shared.db import get_config_value, set_config_value
from shared.models import PseudonymizationMode
from ui.ui_extras import section_spacer

st.title("Configuratie")

# ---------------------------------------------------------------------------
# Threshold-sliders
# ---------------------------------------------------------------------------

_THRESHOLD_KEYS: tuple[tuple[str, str, float], ...] = (
    ("threshold_regex", "Regex-laag (default 1.0)", 1.0),
    ("threshold_spacy_person", "spaCy PER (default 0.85)", 0.85),
    ("threshold_spacy_org", "spaCy ORG (default 0.80)", 0.80),
    ("threshold_spacy_location", "spaCy LOC (default 0.85)", 0.85),
    ("threshold_llm", "LLM (Ollama, default 0.70)", 0.70),
)

# ---------------------------------------------------------------------------
# Generalisering-toggles
# ---------------------------------------------------------------------------

_GENERALIZATION_KEYS: tuple[tuple[str, str], ...] = (
    ("gen_birthdate", "BR-B01: Geboortedatum → geboortejaar"),
    ("gen_postcode", "BR-B02: PC6 → PC2"),
    ("gen_age", "BR-B03: Leeftijd ≥90 → 90+"),
    ("gen_treatment_dates", "BR-B04: Opname/ontslag/exam-datum → YYYY-MM"),
    ("gen_flag_rare_icd", "BR-B05: Zeldzame ICD → review-flag"),
)


def _read_float(key: str, default: float) -> float:
    raw = get_config_value(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _read_bool(key: str, default: bool) -> bool:
    raw = get_config_value(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _render_thresholds() -> None:
    st.subheader("Detectie-thresholds (BR-A04)")
    st.caption(
        "Confidence onder de slider gaat naar de manual-review-queue in plaats "
        "van direct doorstromen. Verhogen = strenger; verlagen = soepeler."
    )
    with st.form("thresholds-form"):
        new_values: dict[str, float] = {}
        for key, label, default in _THRESHOLD_KEYS:
            current = _read_float(key, default)
            new_values[key] = st.slider(label, 0.0, 1.0, current, 0.01, key=f"slider-{key}")
        if st.form_submit_button("Thresholds opslaan"):
            for key, value in new_values.items():
                set_config_value(key, f"{value:.4f}")
            st.success("Thresholds opgeslagen — gelden bij de eerstvolgende request.")


def _render_generalization_toggles() -> None:
    st.subheader("Generalisering (BR-B01..B05)")
    st.caption("Per regel aan/uit. Uit-zetten betekent: lossy transformatie wordt overgeslagen.")
    with st.form("gen-form"):
        new_values: dict[str, bool] = {}
        for key, label in _GENERALIZATION_KEYS:
            new_values[key] = st.checkbox(label, value=_read_bool(key, True), key=f"chk-{key}")
        if st.form_submit_button("Generalisering opslaan"):
            for key, value in new_values.items():
                set_config_value(key, "1" if value else "0")
            st.success("Generalisering-instellingen opgeslagen.")


def _render_super_default() -> None:
    st.subheader("Super-default pseudonimiseringsmodus (BR-C06)")
    raw = get_config_value(
        "super_default_pseudonymization_mode",
        PseudonymizationMode.ONE_WAY.value,
    )
    try:
        current = PseudonymizationMode(raw)
    except ValueError:
        current = PseudonymizationMode.ONE_WAY

    label_map = {
        PseudonymizationMode.ONE_WAY: "One-way (aanbevolen)",
        PseudonymizationMode.TWO_WAY: "Two-way",
    }
    selected_label = st.selectbox(
        "Super-default",
        list(label_map.values()),
        index=list(label_map.values()).index(label_map[current]),
        key="super-default-mode",
    )
    selected = next(mode for mode, label in label_map.items() if label == selected_label)
    if selected is PseudonymizationMode.TWO_WAY:
        st.error(
            "BR-C06 vereist documentatie waarom afwijken van one-way functioneel "
            "noodzakelijk is. Leg dit ook vast in een README of changelog buiten Pylades."
        )
        justification = st.text_area(
            "Documenteer waarom two-way de nieuwe super-default wordt",
            value=get_config_value("super_default_two_way_justification", "") or "",
        )
    else:
        justification = ""

    if st.button("Super-default opslaan", key="save-super-default"):
        if selected is PseudonymizationMode.TWO_WAY and not justification.strip():
            st.error("Two-way zonder onderbouwing mag niet (BR-C06).")
            return
        set_config_value("super_default_pseudonymization_mode", selected.value)
        if justification.strip():
            set_config_value("super_default_two_way_justification", justification.strip())
        st.success(f"Super-default staat nu op `{selected.value}`.")


def _render_key_rotation() -> None:
    st.subheader("Globale HMAC-sleutel roteren")
    st.warning(
        "Na rotatie kunnen **bestaande pseudoniemen niet meer worden teruggevertaald**. "
        "Eerdere audit-rijen blijven leesbaar, maar de pseudoniemen erin zijn "
        "niet langer cryptografisch herleidbaar."
    )
    st.caption(
        "Exporteer bestaande mappings naar CSV vóórdat je roteert. De gebruiker "
        "is zelf verantwoordelijk voor veilige opslag van de export."
    )

    st.download_button(
        "Exporteer mappings naar CSV",
        data=export_mappings_csv(),
        file_name="pylades-vault-mappings.csv",
        mime="text/csv",
        help=(
            "Bevat alle session_id/pseudonym/original-paren plaintext. "
            "Bewaar deze export veilig en versleuteld."
        ),
    )

    with st.form("rotate-form"):
        st.markdown(
            "Bevestig de rotatie door het woord **`ROTEER`** te typen (hoofdlettergevoelig)."
        )
        confirmation = st.text_input("Typed confirmation", key="rotate-confirmation")
        submit = st.form_submit_button("Roteer nu")

    if not submit:
        return
    if confirmation != "ROTEER":
        st.error("Bevestigingstekst klopt niet — typ exact `ROTEER` (hoofdletters).")
        return

    archive_path = rotate_global_secret(settings.global_secret_path)
    st.success(
        f"Gelukt: oude sleutel gearchiveerd als `{archive_path}`. "
        "Nieuwe sleutel is actief; oude pseudoniemen zijn vanaf nu niet meer herleidbaar."
    )


_render_thresholds()
section_spacer()
_render_generalization_toggles()
section_spacer()
_render_super_default()
section_spacer()
_render_key_rotation()
