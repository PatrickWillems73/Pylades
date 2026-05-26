"""Release-versie blijft gesynchroniseerd tussen code en packaging."""

from __future__ import annotations

import re
from pathlib import Path

from shared.version import (
    TARGET_VERSION,
    __version__,
    pylades_display,
    pylades_sidebar_version,
    target_version_display,
    version_display,
)


def test_version_display_format() -> None:
    assert version_display() == f"v{__version__}"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", version_display())


def test_target_version_display() -> None:
    assert target_version_display() == "v0.3"
    assert TARGET_VERSION == "0.3.0"


def test_pylades_display() -> None:
    assert pylades_display().startswith("Pylades v")


def test_pylades_sidebar_version() -> None:
    assert pylades_sidebar_version() == f"{version_display()} POC"


def test_pyproject_version_matches_shared() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None, "pyproject.toml mist [project].version"
    assert match.group(1) == __version__
