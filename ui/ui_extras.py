"""Gedeelde Streamlit-shell: lokale CSS + streamlit-extras (`style_metric_cards`).

Shell-setup (``pylades_set_page_config`` + ``init_pylades_ui``) gebeurt in
``ui/Home.py`` — view-scripts under ``ui/views/`` bevatten alleen inhoud.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Literal

import streamlit as st
import streamlit.components.v1 as components
from streamlit_extras.metric_cards import style_metric_cards

from shared.version import pylades_page_title as _pylades_page_title
from shared.version import version_display
from ui.sidebar_state import apply_sidebar_state
from ui.theme import BRAND_ORANGE, apply_polish, sidebar_logo_data_uri

# Re-export voor pagina's (één import-pad).
pylades_page_title = _pylades_page_title

_FAVICON_PATH = Path(__file__).resolve().parent / "assets" / "favicon.png"
# Horizontaal wordmark (580×154); links uitgelijnd via CSS in `ui/theme.py`.
_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo.png"


def _register_collapsed_sidebar_logo() -> None:
    """Klein icoon in app-header wanneer sidebar dicht (``st.logo`` icon_image)."""
    if not _FAVICON_PATH.is_file():
        return
    image = str(_LOGO_PATH if _LOGO_PATH.is_file() else _FAVICON_PATH)
    st.logo(image, icon_image=str(_FAVICON_PATH), size="large")


def pylades_page_icon() -> str | Path:
    """Tab-/sidebar-icoon voor `st.set_page_config`.

    Het browsertabblad komt vooral van Streamlits `./favicon.png` (zie `ui/favicon_sync.py`).
    Dit pad voedt Streamlits `page_icon` via `image_to_url` (sidebar/titel na laden).
    """
    if _FAVICON_PATH.is_file():
        return _FAVICON_PATH
    return "🔴"


def pylades_set_page_config(
    page: str,
    *,
    layout: Literal["centered", "wide"] = "wide",
    default_collapsed: bool = False,
) -> None:
    """Paginatitel + icoon.

    `default_collapsed=True` alleen op Home — subpagina's erven sidebar-staat via
    localStorage (geen herhaalde collapse-animatie bij menuklik).
    """
    config: dict[str, object] = {
        "page_title": pylades_page_title(page),
        "page_icon": pylades_page_icon(),
        "layout": layout,
    }
    if default_collapsed:
        config["initial_sidebar_state"] = "collapsed"
    st.set_page_config(**config)


def _sidebar_version_label() -> str:
    return f"{version_display()} POC"


def render_sidebar_branding() -> None:
    """Logo + semver onder elkaar, boven de menu-links (st.navigation)."""
    label = html.escape(_sidebar_version_label())
    logo_uri = sidebar_logo_data_uri()
    if logo_uri:
        st.markdown(
            f'<div class="pylades-sidebar-brand">'
            f'<img class="pylades-sidebar-logo" src="{logo_uri}" alt="Pylades">'
            f'<p class="pylades-sidebar-version">{label}</p>'
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<p class="pylades-sidebar-version">{label}</p>',
            unsafe_allow_html=True,
        )


def init_pylades_ui() -> None:
    """Inject Pylades-CSS (o.a. Helvetica) + metric-card-styling uit streamlit-extras."""
    apply_polish()
    _register_collapsed_sidebar_logo()
    apply_sidebar_state()
    style_metric_cards(
        background_color="#171A21",
        border_left_color=BRAND_ORANGE,
        border_color="#2A2F3A",
        border_radius_px=12,
        box_shadow=True,
    )


def section_spacer(*, px: int = 24) -> None:
    """Verticale ademruimte zonder `st.divider()` (geen horizontale streep)."""
    st.space(px)


def section_heading(title: str, *, caption: str | None = None) -> None:
    """Sectietitel zonder omlijning — scheiding via typografie i.p.v. border."""
    st.markdown(
        f'<p class="pylades-section-title">{html.escape(title)}</p>',
        unsafe_allow_html=True,
    )
    if caption:
        st.caption(caption)


def attention_notice(html_body: str) -> None:
    """Geel pending-blok — zelfde status-semantiek als 'Wacht op jouw beoordeling'."""
    st.markdown(
        f'<div class="pylades-notice pylades-notice--attention">{html_body}</div>',
        unsafe_allow_html=True,
    )


def scroll_to_element(element_id: str) -> None:
    """Scroll in het hoofdvenster naar een element-id (Streamlit ≥1.57, geen st.scroll_to)."""
    components.html(
        f"""
        <script>
        (function () {{
            function scroll() {{
                try {{
                    const doc = window.parent.document;
                    const el = doc.getElementById({element_id!r});
                    if (el) {{
                        el.scrollIntoView({{ behavior: "smooth", block: "start" }});
                        return true;
                    }}
                }} catch (err) {{}}
                return false;
            }}
            if (!scroll()) {{
                window.setTimeout(scroll, 150);
            }}
        }})();
        </script>
        """,
        height=0,
    )
