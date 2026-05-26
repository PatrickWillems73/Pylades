"""Vervang Streamlits gebundelde tab-favicon door `ui/assets/favicon.png`.

Streamlit's `index.html` verwijst naar `./favicon.png` in de package-static
(map). `.streamlit/favicon.png` wordt niet gebruikt voor het browsertabblad.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import streamlit

_REPO_FAVICON = Path(__file__).resolve().parent / "assets" / "favicon.png"
_STREAMLIT_FAVICON = Path(streamlit.__file__).resolve().parent / "static" / "favicon.png"


def sync_streamlit_favicon() -> Path | None:
    """Kopieer het Pylades-icoon naar Streamlits static map. Retourneert doelpad."""
    if not _REPO_FAVICON.is_file():
        return None
    _STREAMLIT_FAVICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_REPO_FAVICON, _STREAMLIT_FAVICON)
    return _STREAMLIT_FAVICON
