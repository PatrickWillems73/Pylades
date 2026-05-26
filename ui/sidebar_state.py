"""Sidebar-gedrag: localStorage sync, geen polling/toggles bij navigatie.

Eén event-delegation op `window.parent` (blijft hangen over paginawissels).
Menu open + klik → localStorage expanded vóór navigatie. Refresh → collapsed.
"""

from __future__ import annotations

import streamlit.components.v1 as components

_USER_OPEN_KEY = "pylades_sidebar_user_open"
_STREAMLIT_COLLAPSED_PREFIX = "stSidebarCollapsed-"

_SIDEBAR_STATE_JS = f"""
<script>
(function () {{
  const win = window.parent;
  if (win.__pyladesSidebarV2) {{
    return;
  }}
  win.__pyladesSidebarV2 = true;

  const USER_OPEN_KEY = {_USER_OPEN_KEY!r};
  const COLLAPSED_PREFIX = {_STREAMLIT_COLLAPSED_PREFIX!r};

  function parentDoc() {{
    return win.document;
  }}

  function setStreamlitCollapsed(isCollapsed) {{
    const value = isCollapsed ? "true" : "false";
    for (let i = 0; i < win.localStorage.length; i += 1) {{
      const key = win.localStorage.key(i);
      if (key && key.startsWith(COLLAPSED_PREFIX)) {{
        win.localStorage.setItem(key, value);
      }}
    }}
  }}

  function syncOpenIntent(isOpen) {{
    if (isOpen) {{
      win.sessionStorage.setItem(USER_OPEN_KEY, "1");
      setStreamlitCollapsed(false);
    }} else {{
      win.sessionStorage.removeItem(USER_OPEN_KEY);
      setStreamlitCollapsed(true);
    }}
  }}

  function sidebarExpanded(doc) {{
    const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
    if (!sidebar) {{
      return false;
    }}
    return sidebar.getAttribute("aria-expanded") === "true";
  }}

  win.addEventListener("pageshow", function (event) {{
    const nav = performance.getEntriesByType("navigation")[0];
    if (!nav || nav.type !== "reload") {{
      return;
    }}
    win.sessionStorage.removeItem(USER_OPEN_KEY);
    setStreamlitCollapsed(true);
  }});

  parentDoc().addEventListener(
    "click",
    function (event) {{
      const doc = parentDoc();
      const navLink = event.target.closest('nav[data-testid="stSidebarNav"] a');
      if (navLink) {{
        if (sidebarExpanded(doc)) {{
          syncOpenIntent(true);
        }}
        return;
      }}
      const toggle = event.target.closest('[data-testid="stSidebarCollapseButton"]');
      if (toggle) {{
        win.setTimeout(function () {{
          syncOpenIntent(sidebarExpanded(doc));
        }}, 180);
      }}
    }},
    true,
  );
}})();
</script>
"""


def apply_sidebar_state() -> None:
    """Registreer sidebar-listeners éénmalig op het parent-venster."""
    components.html(_SIDEBAR_STATE_JS, height=0)
