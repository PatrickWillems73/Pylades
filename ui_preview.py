"""Pylades UI-stijl-preview — niet onderdeel van de productie-UI.

Toont vier varianten op dezelfde mock-data zodat je in één blik kunt
vergelijken: vanilla Streamlit, native theme + CSS-polish, streamlit-extras
en streamlit-shadcn-ui. Plus een korte demo van streamlit-option-menu als
sidebar-navigatie.

Start los van de hoofd-UI:
    uv run streamlit run ui_preview.py --server.port 8502
"""

from __future__ import annotations

import sys
from pathlib import Path

for _root in Path(__file__).resolve().parents:
    if (_root / "pyproject.toml").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

from dataclasses import dataclass

import streamlit as st

from ui.ui_extras import init_pylades_ui

# Optionele packages — alleen voor de preview, niet voor de hoofd-UI.
try:
    from streamlit_extras.badges import badge as link_badge  # type: ignore
    from streamlit_extras.colored_header import colored_header  # type: ignore
    from streamlit_extras.metric_cards import style_metric_cards  # type: ignore
    from streamlit_extras.tags import tagger_component  # type: ignore

    EXTRAS_AVAILABLE = True
except ImportError:
    EXTRAS_AVAILABLE = False

try:
    import streamlit_shadcn_ui as shadcn  # type: ignore

    SHADCN_AVAILABLE = True
except ImportError:
    SHADCN_AVAILABLE = False

try:
    from streamlit_option_menu import option_menu  # type: ignore

    OPTION_MENU_AVAILABLE = True
except ImportError:
    OPTION_MENU_AVAILABLE = False


st.set_page_config(page_title="Pylades UI-preview", layout="wide")
init_pylades_ui()


# ---------------------------------------------------------------------------
# Gedeelde mock-data — exact dezelfde input voor elke variant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatusCard:
    name: str
    ok: bool
    message: str


STATUS_CARDS: tuple[StatusCard, ...] = (
    StatusCard("Proxy", True, "FastAPI op :8080 — /healthz returnt ok"),
    StatusCard("Ollama", False, "Niet bereikbaar (optioneel)"),
    StatusCard("spaCy NL", True, "nl_core_news_md geladen"),
    StatusCard("Databases", True, "content + vault geïnitialiseerd"),
)

AUDIT_ROWS = [
    {
        "id": 17,
        "tijd": "2026-05-15 16:42:01",
        "status": "ok",
        "session": "a3f1c9e2",
        "model": "claude-sonnet-4-5",
        "avg_conf": "0.98",
    },
    {
        "id": 16,
        "tijd": "2026-05-15 16:38:55",
        "status": "review",
        "session": "9b2e4d11",
        "model": "claude-sonnet-4-5",
        "avg_conf": "0.71",
    },
    {
        "id": 15,
        "tijd": "2026-05-15 16:34:18",
        "status": "error",
        "session": "4c1a8770",
        "model": "claude-sonnet-4-5",
        "avg_conf": "—",
    },
]

MAPPING_ROWS = [
    {
        "Origineel": "Pietersen",
        "Type": "NAME",
        "Pseudoniem": "[PER-abcdef]",
        "Modus": "TWO_WAY",
        "Confidence": 0.93,
    },
    {
        "Origineel": "123456782",
        "Type": "BSN",
        "Pseudoniem": "[BSN-9f1c20]",
        "Modus": "ONE_WAY",
        "Confidence": 1.00,
    },
    {
        "Origineel": "1011AB",
        "Type": "POSTCODE_PC6",
        "Pseudoniem": "[PC6-3e7a55]",
        "Modus": "ONE_WAY",
        "Confidence": 1.00,
    },
]


# ---------------------------------------------------------------------------
# Variant 1 — vanilla Streamlit (huidige Pylades-stijl)
# ---------------------------------------------------------------------------


def _render_vanilla() -> None:
    st.subheader("Status")
    cols = st.columns(4)
    for col, card in zip(cols, STATUS_CARDS, strict=True):
        with col:
            label = "OK" if card.ok else "Fout"
            st.markdown(f"### {label} — {card.name}")
            if card.ok:
                st.success(card.message)
            else:
                st.error(card.message)

    st.subheader("Audit (recent)")
    st.dataframe(AUDIT_ROWS, use_container_width=True, hide_index=True)

    st.subheader("Mapping-tabel (testrun)")
    st.dataframe(MAPPING_ROWS, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Variant 2 — theme + CSS-polish only (zelfde markup als vanilla)
# ---------------------------------------------------------------------------


def _render_theme_only() -> None:
    st.info(
        "Variant 2 gebruikt **dezelfde markup als Variant 1**. Het verschil zit "
        "in `.streamlit/config.toml` (Pylades-oranje, dark base) + `ui/theme.py` "
        "(spacing, button-radius, footer-hide). Dat zie je hier al — deze hele "
        "preview-pagina rendert ermee."
    )
    _render_vanilla()


# ---------------------------------------------------------------------------
# Variant 3 — streamlit-extras
# ---------------------------------------------------------------------------


def _render_extras() -> None:
    if not EXTRAS_AVAILABLE:
        st.warning("`streamlit-extras` niet geïnstalleerd.")
        return

    colored_header(
        label="Status",
        description="metric-cards uit streamlit-extras",
        color_name="red-70",
    )
    cols = st.columns(4)
    for col, card in zip(cols, STATUS_CARDS, strict=True):
        with col:
            st.metric(
                label=card.name,
                value="OK" if card.ok else "FOUT",
                delta=card.message,
                delta_color="normal" if card.ok else "inverse",
            )
    style_metric_cards(
        background_color="#171A21",
        border_left_color="#F97315",
        border_color="#2A2F3A",
        box_shadow=True,
    )

    colored_header(
        label="Audit (recent)",
        description="status-tags via streamlit-extras `tagger_component`",
        color_name="red-70",
    )
    for row in AUDIT_ROWS:
        col_id, col_session, col_status, col_conf = st.columns([1, 2, 2, 1])
        col_id.write(f"`#{row['id']}`")
        col_session.write(f"session `{row['session']}`")
        with col_status:
            tone_label = {
                "ok": "✓ ok",
                "review": "⚠ review",
                "error": "✗ error",
            }
            tone_color = {
                "ok": "green",
                "review": "orange",
                "error": "red",
            }
            # `badges.badge` is alleen voor externe links (PyPI, GitHub, Streamlit Cloud);
            # voor inline status-pill gebruiken we `tags.tagger_component`.
            tagger_component(
                "",
                [tone_label[row["status"]]],
                color_name=tone_color[row["status"]],
            )
        col_conf.write(f"conf {row['avg_conf']}")
    st.caption(
        "Echte link-badge uit `streamlit_extras.badges` — PyPI-package (vereist "
        "`type` + `name` of `url`, géén vrij statuslabel):"
    )
    link_badge(type="pypi", name="streamlit-extras")


# ---------------------------------------------------------------------------
# Variant 4 — streamlit-shadcn-ui
# ---------------------------------------------------------------------------


def _render_shadcn() -> None:
    if not SHADCN_AVAILABLE:
        st.warning("`streamlit-shadcn-ui` niet geïnstalleerd.")
        return

    shadcn.alert_dialog(
        show=False,
        title="Pylades UI in shadcn-look",
        description="Buttons, badges, cards en alerts uit shadcn/ui-stijl.",
        key="alert-intro",
    )

    st.markdown("### Status")
    cols = st.columns(4)
    for col, card in zip(cols, STATUS_CARDS, strict=True):
        with col:
            shadcn.card(
                title=card.name,
                content=card.message,
                description="OK" if card.ok else "Niet beschikbaar",
                key=f"card-{card.name}",
            )

    st.markdown("### Audit (recent)")
    cols = st.columns([1, 2, 1, 2, 1])
    for label, c in zip(["ID", "Session", "Status", "Model", "Conf"], cols, strict=True):
        c.markdown(f"**{label}**")
    for row in AUDIT_ROWS:
        cid, csess, cstat, cmod, cconf = st.columns([1, 2, 1, 2, 1])
        cid.write(f"`#{row['id']}`")
        csess.write(f"`{row['session']}`")
        variant_map = {"ok": "default", "review": "secondary", "error": "destructive"}
        with cstat:
            # API v0.1.x: `badges([(text, variant), ...])` — géén enkelvoud `badge`.
            shadcn.badges(
                [(row["status"], variant_map[row["status"]])],
                key=f"b-{row['id']}",
            )
        cmod.write(row["model"])
        cconf.write(row["avg_conf"])

    st.markdown("### Mapping-tabel (testrun)")
    cols = st.columns([2, 1, 2, 1, 1])
    for label, c in zip(["Origineel", "Type", "Pseudoniem", "Modus", "Conf"], cols, strict=True):
        c.markdown(f"**{label}**")
    for i, row in enumerate(MAPPING_ROWS):
        co, ct, cp, cm, cc = st.columns([2, 1, 2, 1, 1])
        co.write(row["Origineel"])
        ct.write(f"`{row['Type']}`")
        cp.code(row["Pseudoniem"], language="text")
        mode_variant = "destructive" if row["Modus"] == "TWO_WAY" else "outline"
        with cm:
            shadcn.badges([(row["Modus"], mode_variant)], key=f"mode-{i}")
        cc.write(f"{row['Confidence']:.2f}")


# ---------------------------------------------------------------------------
# Variant 5 — streamlit-option-menu (sidebar-nav)
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Navigatie-stijl")
    if OPTION_MENU_AVAILABLE:
        option_menu(
            menu_title="Pylades",
            options=["Home", "Status", "Prompts", "Review", "Audit", "Config"],
            icons=["house", "card-list", "flask", "hourglass-split", "bar-chart", "gear"],
            menu_icon="droplet-half",
            default_index=0,
            styles={
                "container": {"padding": "0!important", "background-color": "#171A21"},
                "icon": {"color": "#F97315", "font-size": "16px"},
                "nav-link": {
                    "font-size": "14px",
                    "text-align": "left",
                    "margin": "0px",
                    "--hover-color": "#1F242E",
                },
                "nav-link-selected": {"background-color": "#F97315"},
            },
        )
        st.caption(
            "↑ Dit is `streamlit-option-menu`. Vergelijk met de standaard-"
            "page-list die Streamlit zelf rendert wanneer je `ui/pages/*.py` "
            "bestanden hebt."
        )
    else:
        st.warning("`streamlit-option-menu` niet geïnstalleerd.")


# ---------------------------------------------------------------------------
# Hoofd-layout: vier tabs voor het content-vergelijk
# ---------------------------------------------------------------------------

st.title("Pylades UI-stijl-preview")
st.caption(
    "Vergelijk vier styling-strategieën op dezelfde mock-data. "
    "Kleurpalet en font komen uit `.streamlit/config.toml`; de CSS-polish "
    "uit `ui/theme.py`. De sidebar toont een vijfde variant: een vervangende "
    "navigatie via `streamlit-option-menu`."
)

tabs = st.tabs(
    [
        "1. Vanilla Streamlit (huidig)",
        "2. Theme + CSS-polish",
        "3. streamlit-extras",
        "4. streamlit-shadcn-ui",
    ]
)

with tabs[0]:
    _render_vanilla()
with tabs[1]:
    _render_theme_only()
with tabs[2]:
    _render_extras()
with tabs[3]:
    _render_shadcn()
