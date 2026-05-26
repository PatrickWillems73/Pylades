"""Regressie: testrun-modus normalisatie (cookies + query-param)."""

from __future__ import annotations

from ui.cookies import MODE_EXTENDED, MODE_SIMPLIFIED, normalize_testrun_mode


def test_normalize_testrun_mode_accepts_valid_values() -> None:
    assert normalize_testrun_mode(MODE_SIMPLIFIED) == MODE_SIMPLIFIED
    assert normalize_testrun_mode(MODE_EXTENDED) == MODE_EXTENDED


def test_normalize_testrun_mode_maps_legacy_eenvoudig() -> None:
    assert normalize_testrun_mode("Eenvoudig") == MODE_SIMPLIFIED


def test_normalize_testrun_mode_falls_back_to_default() -> None:
    assert normalize_testrun_mode(None) == MODE_SIMPLIFIED
    assert normalize_testrun_mode("") == MODE_SIMPLIFIED
    assert normalize_testrun_mode("Expert") == MODE_SIMPLIFIED
    assert normalize_testrun_mode("Expert", default=MODE_EXTENDED) == MODE_EXTENDED
