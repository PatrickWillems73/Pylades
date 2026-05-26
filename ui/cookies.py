"""Browser-cookies voor persistente UI-voorkeuren (Streamlit Home).

Streamlit 1.57+: `components.v1.html` heeft geen `key` en levert geen return-waarde.
We lezen cookies via een eenmalige client-side redirect naar `?testrun_mode=…`
en houden cookie + query-param in sync bij wijzigingen.
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

MODE_COOKIE_NAME = "pylades_testrun_mode"
MODE_QUERY_PARAM = "testrun_mode"
MODE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 jaar
MODE_SIMPLIFIED = "Vereenvoudigd"
MODE_EXTENDED = "Uitgebreid"
_LEGACY_MODE_ALIASES = {"Eenvoudig": MODE_SIMPLIFIED}
VALID_TESTRUN_MODES = frozenset({MODE_SIMPLIFIED, MODE_EXTENDED})
_COOKIE_HYDRATED_KEY = "_testrun_mode_cookie_hydrated"
_COOKIE_BOOTSTRAP_KEY = "_testrun_mode_cookie_bootstrap_done"
_COOKIE_PERSISTED_KEY = "_testrun_mode_cookie_persisted"

_COOKIE_BOOTSTRAP_JS = f"""
<script>
(function () {{
    const legacy = {{"Eenvoudig": {MODE_SIMPLIFIED!r}}};
    const valid = new Set([{MODE_SIMPLIFIED!r}, {MODE_EXTENDED!r}]);
    const name = {MODE_COOKIE_NAME!r};
    const prefix = name + "=";
    const part = document.cookie.split("; ").find(function (row) {{
        return row.startsWith(prefix);
    }});
    const value = part ? decodeURIComponent(part.slice(prefix.length)) : "";
    const mode = legacy[value] || value;
    if (!valid.has(mode)) {{
        return;
    }}
    try {{
        const top = window.top || window.parent;
        const url = new URL(top.location.href);
        if (url.searchParams.get({MODE_QUERY_PARAM!r}) === mode) {{
            return;
        }}
        url.searchParams.set({MODE_QUERY_PARAM!r}, mode);
        top.location.replace(url.toString());
    }} catch (err) {{
        /* iframe / cross-origin — negeren */
    }}
}})();
</script>
"""


def normalize_testrun_mode(
    value: str | None, *, default: str = MODE_SIMPLIFIED
) -> str:
    """Valideer een modus-string; val terug op `default` bij onbekende waarden."""
    if value in _LEGACY_MODE_ALIASES:
        value = _LEGACY_MODE_ALIASES[value]
    if value in VALID_TESTRUN_MODES:
        return value
    return default


def _apply_mode_to_session(mode: str) -> None:
    st.session_state.testrun_mode = mode
    st.session_state["testrun_mode_pills"] = mode
    st.session_state[_COOKIE_PERSISTED_KEY] = mode


def _render_cookie_bootstrap() -> None:
    """Lees cookie in de browser; redirect eenmalig naar ?testrun_mode=… indien nodig."""
    components.html(_COOKIE_BOOTSTRAP_JS, height=0)


def hydrate_testrun_mode(*, default: str = MODE_SIMPLIFIED) -> None:
    """Herstel `testrun_mode` uit query-param (cookie-bootstrap) vóór `st.pills`."""
    if st.session_state.get(_COOKIE_HYDRATED_KEY):
        return

    qp_raw = st.query_params.get(MODE_QUERY_PARAM)
    if qp_raw in _LEGACY_MODE_ALIASES:
        qp_raw = _LEGACY_MODE_ALIASES[qp_raw]
    qp_mode = normalize_testrun_mode(qp_raw, default=default)
    if qp_raw in VALID_TESTRUN_MODES:
        _apply_mode_to_session(qp_mode)
        st.session_state[_COOKIE_HYDRATED_KEY] = True
        return

    if not st.session_state.get(_COOKIE_BOOTSTRAP_KEY):
        st.session_state[_COOKIE_BOOTSTRAP_KEY] = True
        _render_cookie_bootstrap()

    st.session_state[_COOKIE_HYDRATED_KEY] = True


def persist_testrun_mode(mode: str) -> None:
    """Schrijf modus naar cookie + query-param wanneer de gebruiker wisselt."""
    if mode not in VALID_TESTRUN_MODES:
        return
    if st.session_state.get(_COOKIE_PERSISTED_KEY) == mode:
        return

    st.query_params[MODE_QUERY_PARAM] = mode
    components.html(
        f"""
        <script>
        document.cookie = {MODE_COOKIE_NAME!r} + "=" + encodeURIComponent({mode!r}) +
            "; path=/; max-age={MODE_COOKIE_MAX_AGE}; SameSite=Lax";
        </script>
        """,
        height=0,
    )
    st.session_state[_COOKIE_PERSISTED_KEY] = mode
