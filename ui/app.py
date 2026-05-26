"""Legacy Streamlit-entrypoint — draait `Home.py`.

Gebruik bij voorkeur: `uv run streamlit run ui/Home.py` (sidebar-label «Home»).
Deze shim blijft bestaan voor bestaande scripts en opgeslagen `streamlit run`-paden.
"""

from __future__ import annotations

import runpy
from pathlib import Path

_home = Path(__file__).resolve().parent / "Home.py"
runpy.run_path(str(_home), run_name="__main__")
