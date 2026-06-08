"""Gedeelde Streamlit-shell: lokale CSS + streamlit-extras (`style_metric_cards`).

Shell-setup (``pylades_set_page_config`` + ``init_pylades_ui``) gebeurt in
``ui/Home.py`` — view-scripts under ``ui/views/`` bevatten alleen inhoud.
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path
from typing import Literal

import streamlit as st
import streamlit.components.v1 as components
from markdown_it import MarkdownIt
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


_LLM_MARKDOWN = MarkdownIt("commonmark", {"html": False})
_UNICODE_BULLET_RE = re.compile(r"^(\s*)•\s")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-+*]|\d+\.)\s")


def normalize_llm_markdown(text: str) -> str:
    """Maak LLM-markdown compacter vóór render.

    - Zet unicode bullets (`•`) om naar `-`
    - Collapseert dubbele+ lege regels tot één
    - Verwijdert lege regels tussen opeenvolgende lijstitems (voorkomt
      'loose lists' met extra ``<p>``-marges in de HTML)
    """
    lines: list[str] = []
    for line in text.splitlines():
        if "•" in line:
            line = _UNICODE_BULLET_RE.sub(r"\1- ", line)
        lines.append(line.rstrip())

    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        if not line.strip():
            if prev_blank:
                continue
            prev_blank = True
            collapsed.append("")
            continue
        prev_blank = False
        collapsed.append(line)

    compact: list[str] = []
    for idx, line in enumerate(collapsed):
        if line == "" and compact and _LIST_ITEM_RE.match(compact[-1]):
            nxt = next(
                (collapsed[j] for j in range(idx + 1, len(collapsed)) if collapsed[j] != ""),
                None,
            )
            if nxt and _LIST_ITEM_RE.match(nxt):
                continue
        compact.append(line)
    return "\n".join(compact).strip()


def llm_markdown_to_html(text: str) -> str:
    """Render LLM-markdown veilig naar HTML (geen ruwe HTML-tags uit upstream)."""
    return _LLM_MARKDOWN.render(normalize_llm_markdown(text))


_DOCS_FONT = "Helvetica,Arial,sans-serif"
_DOCS_BODY = (
    f'font-size:11pt;font-family:{_DOCS_FONT};color:#000000;'
)
_HEADING_RE = re.compile(r"<h([1-4])>(.*?)</h\1>", re.DOTALL)
_LI_RE = re.compile(r"<li>(.*?)</li>", re.DOTALL)


def _docs_heading_paragraph(level: int, inner: str) -> str:
    sizes = {1: (17, 12, 6), 2: (15, 10, 4), 3: (13, 8, 3), 4: (12, 6, 2)}
    pt, top, bottom = sizes[level]
    return (
        f'<p style="line-height:1.38;margin-top:{top}pt;margin-bottom:{bottom}pt;">'
        f'<span style="font-size:{pt}pt;font-family:{_DOCS_FONT};'
        f'font-weight:700;color:#000000;">{inner}</span></p>'
    )


def html_fragment_to_docs_clipboard_html(fragment: str) -> str:
    """Maak Google Docs-vriendelijke HTML voor het klembord.

    Docs negeert vaak kale ``<h1>``-tags; gestylede ``<p>``/``<span>`` plus
    StartFragment-markers behouden koppen en lijsten bij plakken.
    """

    def _heading_sub(match: re.Match[str]) -> str:
        return _docs_heading_paragraph(int(match.group(1)), match.group(2))

    styled = _HEADING_RE.sub(_heading_sub, fragment)
    styled = re.sub(
        r"<p>(.*?)</p>",
        lambda m: (
            '<p style="line-height:1.38;margin-top:0pt;margin-bottom:6pt;">'
            f'<span style="{_DOCS_BODY}">{m.group(1)}</span></p>'
        ),
        styled,
        flags=re.DOTALL,
    )
    styled = styled.replace(
        "<ul>",
        '<ul style="margin-top:0pt;margin-bottom:6pt;padding-left:36pt;">',
    )
    styled = styled.replace(
        "<ol>",
        '<ol style="margin-top:0pt;margin-bottom:6pt;padding-left:36pt;">',
    )

    def _li_sub(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.startswith("<p "):
            return f"<li>{inner}</li>"
        return (
            f'<li style="margin:0 0 2pt 0;">'
            f'<span style="{_DOCS_BODY}">{inner}</span></li>'
        )

    styled = _LI_RE.sub(_li_sub, styled)
    return (
        "<!DOCTYPE html><html><head>"
        '<meta http-equiv="content-type" content="text/html; charset=utf-8">'
        "</head><body><!--StartFragment-->"
        f"{styled}<!--EndFragment--></body></html>"
    )


def llm_markdown_to_docs_clipboard_html(text: str) -> str:
    """Markdown → HTML fragment → Google Docs klembord-payload."""
    return html_fragment_to_docs_clipboard_html(llm_markdown_to_html(text))


_CLIPBOARD_COPY_JS = """
<script>
(function () {
    const host = window.parent;
    if (host.__pyladesCopyBound) {
        return;
    }
    host.__pyladesCopyBound = true;
    function b64ToUtf8(b64) {
        const binary = host.atob(b64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return new host.TextDecoder("utf-8").decode(bytes);
    }
    host.document.addEventListener("click", function (event) {
        const btn = event.target.closest(".pylades-copy-btn");
        if (!btn) {
            return;
        }
        event.preventDefault();
        const panel = btn.closest(".pylades-llm-response-panel");
        const body = panel && panel.querySelector(".pylades-llm-response");
        if (!body) {
            return;
        }
        const b64 = body.getAttribute("data-clipboard-html-b64");
        const html = b64
            ? b64ToUtf8(b64)
            : "<!DOCTYPE html><html><body><!--StartFragment-->"
                + body.innerHTML
                + "<!--EndFragment--></body></html>";
        const plain = body.innerText;
        function showCopied() {
            const prev = btn.textContent;
            btn.textContent = "Gekopieerd";
            btn.classList.add("pylades-copy-btn--done");
            host.setTimeout(function () {
                btn.textContent = prev;
                btn.classList.remove("pylades-copy-btn--done");
            }, 1800);
        }
        if (host.ClipboardItem && host.navigator.clipboard.write) {
            const enc = new host.TextEncoder();
            host.navigator.clipboard.write([
                new host.ClipboardItem({
                    "text/html": new Blob(
                        [enc.encode(html)],
                        { type: "text/html;charset=utf-8" },
                    ),
                    "text/plain": new Blob(
                        [enc.encode(plain)],
                        { type: "text/plain;charset=utf-8" },
                    ),
                }),
            ]).then(showCopied).catch(function () {
                host.navigator.clipboard.writeText(plain).then(showCopied);
            });
            return;
        }
        host.navigator.clipboard.writeText(plain).then(showCopied).catch(function () {});
    });
})();
</script>
"""


def _register_clipboard_copy_handler() -> None:
    """Eén delegatie-listener voor `.pylades-copy-btn` in het hoofdvenster."""
    components.html(_CLIPBOARD_COPY_JS, height=0)


def init_pylades_ui() -> None:
    """Inject Pylades-CSS (o.a. Helvetica) + metric-card-styling uit streamlit-extras."""
    apply_polish()
    _register_collapsed_sidebar_logo()
    _register_clipboard_copy_handler()
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


def render_llm_response_panel(
    text: str,
    *,
    anchor_id: str,
    accent: bool = False,
) -> None:
    """LLM-antwoordblok met titel, markdown-inhoud en kopieerknop rechtsboven.

    Met ``accent=True`` krijgt het blok extra attentiewaarde (zwaardere rand +
    subtiele arcering) zodat het antwoord direct opvalt.
    """
    body_html = llm_markdown_to_html(text)
    clipboard_html = html_fragment_to_docs_clipboard_html(body_html)
    clipboard_b64 = base64.b64encode(clipboard_html.encode("utf-8")).decode("ascii")
    panel_class = "pylades-llm-response-panel"
    if accent:
        panel_class += " pylades-llm-response-panel--accent"
    st.markdown(
        f'<div id="{html.escape(anchor_id)}" tabindex="-1"></div>'
        f'<div class="{panel_class}">'
        f'<div class="pylades-llm-response-panel__header">'
        f'<p class="pylades-section-title">Antwoord van het externe LLM</p>'
        f'<button type="button" class="pylades-copy-btn" '
        f'aria-label="Kopieer antwoord als HTML naar klembord">Kopieer</button>'
        f"</div>"
        f'<div class="pylades-llm-response" data-clipboard-html-b64="{clipboard_b64}">'
        f"{body_html}</div>"
        f"</div>",
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
