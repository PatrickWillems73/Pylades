"""Pylades — Status-pagina.

Vier status-kaartjes voor de afhankelijkheden (proxy, Ollama-optioneel,
DEDUCE, databases) met per failend onderdeel een copy-pasteable shell-
command. De daadwerkelijke checks leven in `ui/status.py` zodat ze
testbaar zijn zonder Streamlit-context.

Was tot v0.1.3 de homepagina; sinds v0.2.0 staat de testrun-functionaliteit
op de Home en is dit overzicht naar Status verhuisd zodat niet-technische
gebruikers direct in de probeer-flow landen.
"""

from __future__ import annotations

import sys
from pathlib import Path

# `streamlit run` zet alleen de script-directory op sys.path, niet de project-
# root. Zonder onderstaande shim faalt elke `from ui.* import` / `from shared.*
# import`. Walk-up tot we `pyproject.toml` zien zodat de invocatie cwd-onafhankelijk werkt.
for _root in Path(__file__).resolve().parents:
    if (_root / "pyproject.toml").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

import html
from typing import Any

import streamlit as st

from shared.version import pylades_display
from ui.status import StatusCheck, run_all_checks
from ui.ui_extras import section_spacer

_STATUS_CARD_BG = "#171A21"
_STATUS_CARD_BORDER = "#2A2F3A"
_OK_LEFT = "#2E7D32"
_ERR_LEFT = "#A0263A"


def _render_status_metric(column: Any, check: StatusCheck) -> None:
    """Status-kaart (zelfde look als `style_metric_cards`, met groene/rode linkerrand)."""
    left = _OK_LEFT if check.ok else _ERR_LEFT
    label = html.escape(check.name)
    value = html.escape("OK" if check.ok else "Fout")
    with column:
        st.markdown(
            f"""
<div style="
  background-color:{_STATUS_CARD_BG};
  border:1px solid {_STATUS_CARD_BORDER};
  border-radius:12px;
  border-left:0.5rem solid {left};
  padding:0.65rem 0.75rem 0.65rem 1rem;
  box-shadow:0 0.15rem 1.75rem 0 rgba(58, 59, 69, 0.15);
  margin-bottom:0.35rem;
">
  <div style="font-size:0.875rem;color:rgba(232,230,225,0.75);">{label}</div>
  <div style="font-size:1.65rem;font-weight:600;color:#E8E6E1;">{value}</div>
</div>
            """,
            unsafe_allow_html=True,
        )
        st.caption(check.message)
        if not check.ok:
            if check.fix_hint:
                st.caption(check.fix_hint)
            if check.fix_command:
                st.code(check.fix_command, language="bash")


def render() -> None:
    st.title("Status")
    st.markdown(
        """
Status van de afhankelijkheden die Pylades nodig heeft om te draaien. Bij
een rood bolletje vind je onder de kaart een commando om het probleem op
te lossen.
"""
    )

    if st.button("Status opnieuw controleren", help="Forceer een nieuwe ronde checks"):
        st.rerun()

    checks = run_all_checks()
    cols = st.columns(len(checks))
    for col, check in zip(cols, checks, strict=True):
        _render_status_metric(col, check)

    section_spacer()
    st.markdown(
        f"""
### Architectuur in het kort

- **Proxy** (FastAPI, poort 8080) — zet gevoelige tekst om in pseudoniemen
  voordat hij naar het externe LLM gaat en vertaalt selectief terug op de
  response.
- **UI** (Streamlit, poort 8501) — testruns, opdrachten, review-queue,
  audit en configuratie.
- **Content-DB** (`pylades-content.db`) — opdrachten, audit-log, sessies,
  review-queue, configuratie.
- **Vault-DB** (`pylades-vault.db`, file-mode `0o600`) — pseudoniem ↔
  origineel mappings.

### Belangrijke disclaimers

- {pylades_display()} is een **lokale POC**, geen productie-grade tool.
- `original_prompt` wordt in `pylades-content.db` plaintext bewaard
  (BR-G01); bescherm het host-systeem zelf.
- De vault is alleen door owner leesbaar (`0o600`); rotatie via de
  Config-pagina invalideert oude pseudoniemen.
"""
    )


render()
