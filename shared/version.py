"""Pylades release-versie — enige bron van waarheid.

Wijzig alleen ``__version__`` hier; houd ``pyproject.toml`` [project].version
in sync (gecontroleerd door ``tests/test_version.py``).

``TARGET_VERSION`` is de spec-/milestone-doelversie (PLAN, SPEC-v0.3),
niet de huidige release.
"""

from __future__ import annotations

__version__ = "0.2.7"
TARGET_VERSION = "0.3.0"

PRODUCT_NAME = "Pylades"
TAGLINE = "Een metgezel aan wie je je dossier toevertrouwt"


def version_display() -> str:
    """Semver met v-prefix, bv. ``v0.2.2``."""
    return f"v{__version__}"


def target_version_display() -> str:
    """Doelversie voor documentatie, bv. ``v0.3`` (uit ``0.3.0``)."""
    major, minor, patch = (int(p) for p in TARGET_VERSION.split("."))
    if patch == 0:
        return f"v{major}.{minor}"
    return f"v{TARGET_VERSION}"


def pylades_display() -> str:
    """``Pylades v0.2.2``."""
    return f"{PRODUCT_NAME} {version_display()}"


def pylades_page_title(page: str) -> str:
    """Browsertitel: ``Pylades v0.2.2 — Opdrachten``."""
    return f"{pylades_display()} — {page}"


def pylades_home_title() -> str:
    """Homepage H1."""
    return f"{pylades_display()} — {TAGLINE}"


def pylades_sidebar_version() -> str:
    """Sidebar onder het logo: ``v0.2.2 POC``."""
    return f"{version_display()} POC"
