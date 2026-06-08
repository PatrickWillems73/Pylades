"""Inline CSS-polish voor Pylades' Streamlit-UI.

Streamlit's eigen theme (`.streamlit/config.toml`) regelt kleur en font;
deze module verzorgt de fijnafstelling die theming-flags niet bereiken:
strakker spacing, modernere button-radii, hover-transitions en het
verbergen van de default-footer. Importeer en call `apply_polish()`
bovenaan elke entry-pagina.

Kleurconstanten hier zijn de bron van waarheid voor status-UI; zie ook
`.cursor/rules/ui-color-status.mdc`.
"""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

# Merk-accent (knoppen, sidebar-titels, metric-cards)
BRAND_ORANGE = "#F97315"

# Status-semantiek (linkerranden + arcering)
STATUS_GREEN = "#2E7D32"
# Gedempt staalblauw — zelfde diepte/verzadiging als groen (#2E7D32) en amber (#D4A017).
STATUS_BLUE = "#256F8A"
STATUS_YELLOW = "#D4A017"
STATUS_ERROR = "#A0263A"

# Arcering: doorschijnende variant van de statuskleur (zelfde RGB, lagere alpha).
_HIGHLIGHT_ALPHA = 0.50


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _highlight_style(hex_color: str, *, dashed: bool = False) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    alpha = _HIGHLIGHT_ALPHA
    style = (
        f"background-color: rgba({r}, {g}, {b}, {alpha}); "
        "color: inherit; padding: 1px 3px; border-radius: 3px;"
    )
    if dashed:
        border_alpha = min(alpha + 0.3, 0.85)
        style += (
            f" border: 1px dashed rgba({r}, {g}, {b}, {border_alpha});"
        )
    return style


HIGHLIGHT_ONE_WAY = _highlight_style(STATUS_GREEN)
HIGHLIGHT_TWO_WAY = _highlight_style(STATUS_BLUE)
HIGHLIGHT_PENDING = _highlight_style(STATUS_YELLOW, dashed=True)

_y_r, _y_g, _y_b = _hex_to_rgb(STATUS_YELLOW)
_NOTICE_ATTENTION_BG_ALPHA = 0.35

# Sidebar-branding (logo embedded in CSS — geen st.logo / geen img-load per pagina)
_SIDEBAR_LOGO_HEIGHT = "2.5rem"
_SIDEBAR_LOGO_WIDTH = "9.42rem"  # 2.5rem × (580/154)
_LOGO_ASSET = Path(__file__).resolve().parent / "assets" / "logo.png"


def _sidebar_logo_data_uri() -> str | None:
    if not _LOGO_ASSET.is_file():
        return None
    encoded = base64.standard_b64encode(_LOGO_ASSET.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def sidebar_logo_data_uri() -> str | None:
    """Data-URI voor sidebar-logo (embedded PNG, geen netwerk-load)."""
    return _sidebar_logo_data_uri()


def _sidebar_branding_css() -> str:
    """Vaste styling voor logo + semver-blok in de sidebar."""
    return f"""
  section[data-testid="stSidebar"] .pylades-sidebar-brand {{
    margin: 0.75rem 0 0 0;
    padding: 0 0 1.25rem 0;
  }}
  section[data-testid="stSidebar"] .pylades-sidebar-logo {{
    display: block;
    width: {_SIDEBAR_LOGO_WIDTH};
    max-width: calc(100% - 0.25rem);
    height: {_SIDEBAR_LOGO_HEIGHT};
    min-height: {_SIDEBAR_LOGO_HEIGHT};
    object-fit: contain;
    object-position: left center;
    margin: 0;
  }}
  section[data-testid="stSidebar"] .pylades-sidebar-version {{
    color: rgba(232, 230, 225, 0.42);
    font-size: 0.72rem;
    font-weight: 400;
    letter-spacing: 0.03em;
    line-height: 1.2;
    margin: 0.5rem 0 0 0;
    padding: 0;
    text-align: left;
  }}
  section[data-testid="stSidebar"][aria-expanded="true"] [data-testid="stSidebarHeader"] {{
    min-height: 0 !important;
    height: auto !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: visible !important;
  }}
  section[data-testid="stSidebar"][aria-expanded="true"] [data-testid="stLogoSpacer"] {{
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
  }}
  section[data-testid="stSidebar"][aria-expanded="true"] [data-testid="stSidebarLogo"] {{
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    padding: 0 !important;
    margin: 0 !important;
    overflow: hidden !important;
  }}
  section[data-testid="stSidebar"][aria-expanded="false"] [data-testid="stLogoSpacer"] {{
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
  }}
  section[data-testid="stSidebar"],
  section[data-testid="stSidebar"] *,
  [data-testid="stSidebarContent"],
  [data-testid="stSidebarContent"] * {{
    transition: none !important;
    animation: none !important;
  }}"""


def _build_css() -> str:
    branding = _sidebar_branding_css()
    return f"""
<style>
  /* Helvetica-stack (Streamlit-theme kent alleen generieke "sans serif") */
  html, body,
  .stApp,
  [data-testid="stAppViewContainer"],
  .stMarkdown, p, label,
  h1, h2, h3, h4,
  div[data-testid="stMetric"],
  .stTextInput label,
  .stSelectbox label,
  .stSlider label {{
    font-family: "Helvetica Neue", Helvetica, Arial, sans-serif !important;
  }}

  .stMarkdown span {{
    font-family: inherit;
  }}

  hr {{
    display: none !important;
    height: 0 !important;
    margin: 0 !important;
    border: none !important;
  }}

  .block-container {{ padding-top: 2rem; padding-bottom: 4rem; }}

  .stButton > button {{
    border-radius: 10px;
    font-weight: 500;
    transition: transform 0.05s ease, box-shadow 0.15s ease;
  }}
  .stButton > button:hover {{
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(249, 115, 21, 0.18);
  }}

  /* Tertiary = link-stijl: grijs, onderstreept, geen knop-chrome. */
  .stButton > button[kind="tertiary"],
  .stButton > button[data-testid*="tertiary"] {{
    color: rgba(232, 230, 225, 0.5) !important;
    text-decoration: underline;
    text-underline-offset: 2px;
    font-weight: 400;
    padding-left: 0.25rem;
    padding-right: 0.25rem;
  }}
  .stButton > button[kind="tertiary"]:hover,
  .stButton > button[data-testid*="tertiary"]:hover {{
    color: rgba(232, 230, 225, 0.82) !important;
    transform: none;
    box-shadow: none;
  }}

  pre, code {{ border-radius: 8px !important; }}

  div[data-testid="stVerticalBlockBorderWrapper"] {{
    border-radius: 12px !important;
    border: 1px solid rgba(42, 47, 58, 0.45) !important;
    background: rgba(23, 26, 33, 0.35) !important;
    box-shadow: none !important;
  }}

  .stTextArea textarea,
  .stTextInput input,
  div[data-baseweb="select"] > div {{
    border-color: rgba(42, 47, 58, 0.55) !important;
    background-color: rgba(15, 17, 21, 0.6) !important;
  }}

  .pylades-soft-panel {{
    background: rgba(255, 255, 255, 0.03);
    border-radius: 8px;
    padding: 0.65rem 0.85rem;
    margin-bottom: 0.5rem;
    border-left: 3px solid rgba(42, 47, 58, 0.8);
  }}
  .pylades-soft-panel--one-way {{ border-left-color: {STATUS_GREEN}; }}
  .pylades-soft-panel--two-way {{ border-left-color: {STATUS_BLUE}; }}
  .pylades-soft-panel--pending {{ border-left-color: {STATUS_YELLOW}; }}

  .pylades-accent-strip {{
    background: rgba(23, 26, 33, 0.55);
    border-radius: 10px;
    border-left: 4px solid rgba(42, 47, 58, 0.8);
    padding: 0.85rem 1rem;
    margin-bottom: 1rem;
  }}
  .pylades-accent-strip--ok {{ border-left-color: {STATUS_GREEN}; }}
  .pylades-accent-strip--attention {{ border-left-color: {STATUS_YELLOW}; }}
  .pylades-accent-strip--error {{ border-left-color: {STATUS_ERROR}; }}

  .pylades-notice {{
    border-radius: 10px;
    padding: 0.85rem 1rem;
    margin-bottom: 1rem;
    line-height: 1.5;
    font-size: 0.95rem;
  }}
  .pylades-notice--attention {{
    background: rgba({_y_r}, {_y_g}, {_y_b}, {_NOTICE_ATTENTION_BG_ALPHA});
    border-left: 4px solid {STATUS_YELLOW};
    color: rgba(240, 224, 176, 0.96);
  }}
  .pylades-notice--attention strong {{
    color: {STATUS_YELLOW};
    font-weight: 600;
  }}

  .pylades-llm-response-panel {{
    margin-bottom: 0.75rem;
    border-radius: 0.5rem;
    overflow: hidden;
    background: rgba(255, 255, 255, 0.04);
    border-left: 3px solid {STATUS_GREEN};
  }}
  .pylades-llm-response-panel--accent {{
    border-left-width: 10px;
    background: {STATUS_GREEN}33;
    box-shadow: 0 0 0 2px {STATUS_GREEN}B3, 0 10px 30px rgba(0, 0, 0, 0.42);
  }}
  .pylades-llm-response-panel--accent .pylades-llm-response-panel__header {{
    padding-top: 0.8rem;
  }}
  .pylades-llm-response-panel__header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.65rem 1rem 0.35rem;
  }}
  .pylades-llm-response-panel__header .pylades-section-title {{
    margin: 0;
  }}
  .pylades-copy-btn {{
    flex-shrink: 0;
    background: transparent;
    border: none;
    color: rgba(232, 230, 225, 0.5);
    text-decoration: underline;
    text-underline-offset: 2px;
    font-size: 0.85rem;
    font-weight: 400;
    cursor: pointer;
    padding: 0.15rem 0.25rem;
    font-family: inherit;
  }}
  .pylades-copy-btn:hover {{
    color: rgba(232, 230, 225, 0.82);
  }}
  .pylades-copy-btn--done {{
    color: {STATUS_GREEN};
    text-decoration: none;
  }}
  .pylades-llm-response {{
    background: transparent;
    padding: 0.35rem 1rem 1rem;
    border-radius: 0;
    word-break: break-word;
    line-height: 1.5;
    border-left: none;
  }}
  .pylades-llm-response-panel .pylades-llm-response {{
    margin: 0;
  }}
  .pylades-llm-response h1,
  .pylades-llm-response h2,
  .pylades-llm-response h3,
  .pylades-llm-response h4 {{
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0.65rem 0 0.25rem 0;
    line-height: 1.3;
  }}
  .pylades-llm-response h1 {{
    font-size: 1.15rem;
  }}
  .pylades-llm-response h2 {{
    font-size: 1.05rem;
  }}
  .pylades-llm-response h3,
  .pylades-llm-response h4 {{
    font-size: 0.98rem;
  }}
  .pylades-llm-response h1:first-child,
  .pylades-llm-response h2:first-child,
  .pylades-llm-response h3:first-child {{
    margin-top: 0;
  }}
  .pylades-llm-response p {{
    margin: 0.25rem 0;
  }}
  .pylades-llm-response li > p {{
    margin: 0;
  }}
  .pylades-llm-response ul,
  .pylades-llm-response ol {{
    margin: 0.2rem 0 0.35rem 0;
    padding-left: 1.35rem;
  }}
  .pylades-llm-response li {{
    margin: 0.1rem 0;
  }}
  .pylades-llm-response ul + ul,
  .pylades-llm-response ol + ol {{
    margin-top: 0.15rem;
  }}
  .pylades-llm-response strong {{
    font-weight: 600;
  }}
  .pylades-llm-response a {{
    color: rgba(232, 230, 225, 0.85);
    text-decoration: underline;
    text-underline-offset: 2px;
  }}
  .pylades-llm-response code {{
    font-size: 0.88em;
    background: rgba(255, 255, 255, 0.06);
    padding: 0.1rem 0.3rem;
    border-radius: 4px;
  }}

  .pylades-section-title {{
    font-size: 0.95rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0 0 0.35rem 0;
    opacity: 0.92;
  }}

  .pylades-inline-status {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.9rem;
    opacity: 0.9;
    margin: 0.75rem 0 1rem 0;
    color: rgba(232, 230, 225, 0.85);
  }}
  .pylades-inline-status__dot {{
    flex-shrink: 0;
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 50%;
    background: {STATUS_YELLOW};
    animation: pylades-status-pulse 1s ease-in-out infinite;
  }}
  @keyframes pylades-status-pulse {{
    0%, 100% {{ opacity: 0.35; transform: scale(0.92); }}
    50% {{ opacity: 1; transform: scale(1); }}
  }}

  .pylades-progress {{
    background: rgba(23, 26, 33, 0.55);
    border-radius: 10px;
    border: 1px solid rgba(42, 47, 58, 0.55);
    padding: 0.85rem 1rem;
    margin: 0.5rem 0 1rem 0;
  }}
  .pylades-progress__title {{
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    opacity: 0.92;
    margin-bottom: 0.5rem;
  }}
  .pylades-progress__list {{
    list-style: none;
    margin: 0;
    padding: 0;
  }}
  .pylades-progress__row {{
    display: grid;
    grid-template-columns: 4rem 1fr auto;
    align-items: baseline;
    gap: 0.75rem;
    padding: 0.3rem 0.6rem;
    border-radius: 6px;
    border-left: 3px solid rgba(42, 47, 58, 0.8);
    margin-bottom: 0.3rem;
    font-size: 0.9rem;
    line-height: 1.4;
  }}
  .pylades-progress__row:last-child {{ margin-bottom: 0; }}
  .pylades-progress__step {{
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.55;
  }}
  .pylades-progress__label {{
    color: rgba(232, 230, 225, 0.92);
  }}
  .pylades-progress__status {{
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    opacity: 0.9;
  }}
  .pylades-progress__row--done {{
    border-left-color: {STATUS_GREEN};
  }}
  .pylades-progress__row--done .pylades-progress__status {{
    color: {STATUS_GREEN};
  }}
  .pylades-progress__row--running {{
    border-left-color: {STATUS_YELLOW};
    background: rgba({_y_r}, {_y_g}, {_y_b}, 0.10);
  }}
  .pylades-progress__row--running .pylades-progress__status {{
    color: {STATUS_YELLOW};
  }}
  .pylades-progress__row--running .pylades-progress__status::before {{
    content: "";
    display: inline-block;
    width: 0.5rem;
    height: 0.5rem;
    margin-right: 0.4rem;
    border-radius: 50%;
    background: {STATUS_YELLOW};
    animation: pylades-status-pulse 1s ease-in-out infinite;
  }}
  .pylades-progress__row--unavailable {{
    border-left-color: {STATUS_ERROR};
  }}
  .pylades-progress__row--unavailable .pylades-progress__status {{
    color: {STATUS_ERROR};
  }}
  .pylades-progress__row--disabled .pylades-progress__label,
  .pylades-progress__row--disabled .pylades-progress__status,
  .pylades-progress__row--pending .pylades-progress__label,
  .pylades-progress__row--pending .pylades-progress__status {{
    opacity: 0.5;
  }}

  footer {{ visibility: hidden; }}
  div[data-testid="stToolbar"] {{ right: 1rem; }}
  /* ⋮-menu + running/Stop verbergen; sidebar in-/uitklap-knop blijft in stToolbar. */
  [data-testid="stMainMenu"],
  [data-testid="stStatusWidget"] {{
    display: none !important;
    visibility: hidden !important;
    pointer-events: none !important;
  }}

  h1, h2, h3 {{ letter-spacing: -0.01em; }}
  h1 {{ margin-bottom: 0.4em; }}

  section[data-testid="stSidebar"] h2 {{
    color: {BRAND_ORANGE};
    font-size: 1.1rem;
  }}

{branding}

  section[data-testid="stSidebar"] nav[data-testid="stSidebarNav"] {{
    position: relative !important;
    z-index: 0 !important;
    margin-top: 0 !important;
    padding-top: 0.25rem !important;
    clear: both;
  }}
  section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] {{
    min-height: 2.125rem !important;
    display: flex !important;
    align-items: center !important;
  }}
  section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {{
    position: absolute !important;
    top: 0.5rem !important;
    right: 0.5rem !important;
    z-index: 2 !important;
  }}
</style>
"""


def apply_polish() -> None:
    """Inject de CSS-polish (idempotent — Streamlit dedupliceert dubbele HTML)."""
    st.markdown(_build_css(), unsafe_allow_html=True)
