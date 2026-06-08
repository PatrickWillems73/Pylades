"""Regressie-gate op de detectie-pijplijn via het eval-harnas.

Harde eis (TESTPLAN.md §6 / §12): geen lek van `DIRECT_IDENTIFIER` op de
niet-adversariële (normal) subset. De adversariële records bevatten bewuste
detector-gaten (bv. `ADDRESS`/`DIAGNOSIS`) en worden gerapporteerd, niet gegate.
"""

from __future__ import annotations

import pytest

from eval.evaluate import evaluate
from eval.generators.bootstrap import build_records
from eval.runners.pylades_pipeline import PyladesPipelineRunner
from eval.validators import validate_dataset


def test_bootstrap_dataset_is_internally_consistent() -> None:
    report = validate_dataset(build_records())
    assert report.ok, f"Bootstrap-dataset ongeldig: {report.errors}"
    assert report.entity_count > 0


@pytest.fixture(scope="module")
def normal_report() -> dict:
    records = [r for r in build_records() if r.difficulty == "normal"]
    return evaluate(records, PyladesPipelineRunner())


def test_no_direct_identifier_leaks_on_normal_subset(normal_report: dict) -> None:
    leaks = normal_report["leaks"]
    assert leaks["leak_rate"] == 0.0, (
        f"Direct-identifier-lek gedetecteerd: {leaks['items']}"
    )


def test_report_has_expected_structure(normal_report: dict) -> None:
    for key in ("scores", "confusion", "leaks", "exposure", "latency", "per_record"):
        assert key in normal_report
    assert "exact" in normal_report["scores"]
    assert "overlap" in normal_report["scores"]


def test_full_dataset_runs_and_reports_known_gaps() -> None:
    # De volledige set (incl. adversarial) moet draaien en de bekende
    # clinical-gap (DIAGNOSIS zonder detector) als blootstelling rapporteren.
    report = evaluate(build_records(), PyladesPipelineRunner())
    clinical = report["exposure"]["clinical_sensitive"]
    assert clinical["exposed"] >= 1
