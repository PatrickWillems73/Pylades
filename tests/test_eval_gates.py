"""Regressie-gate op de detectie-pijplijn via het eval-harnas.

Harde eis (TESTPLAN.md §6 / §12): geen lek van `DIRECT_IDENTIFIER` op de
niet-adversariële (normal) subset. De adversariële records bevatten bewuste
detector-gaten (bv. `ADDRESS`/`DIAGNOSIS`) en worden gerapporteerd, niet gegate.

P0-regressie: gepinde synthetische normal-records `syn_029` en `syn_060`
(direct-leak-triage) blijven 0 direct-lek op de deduce-pijplijn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.evaluate import evaluate
from eval.generators.bootstrap import build_records
from eval.runners.base import RunOutput
from eval.runners.pylades_pipeline import PyladesPipelineRunner
from eval.schema import EvalRecord, load_jsonl
from eval.validators import validate_dataset

_PINNED_NORMAL_LEAK_RECORDS = ("syn_029", "syn_060")
# P4: ex-GLiNER adversarial leaks — gedekt door rol-heuristiek (geen transformer).
_PINNED_ADVERSARIAL_LEAK_RECORDS = ("syn_112", "syn_120", "syn_121", "syn_122", "syn_140")
_SYNTHETIC_DATASET = Path("eval/datasets/synthetic/dataset.jsonl")


@pytest.fixture(scope="module")
def synthetic_records_by_id() -> dict[str, EvalRecord]:
    return {r.id: r for r in load_jsonl(_SYNTHETIC_DATASET)}


@pytest.fixture(scope="module")
def deduce_runner():
    pytest.importorskip("deduce")
    from eval.runners.ner_backends import DeduceBackend
    from eval.runners.ner_pipeline import NerPipelineRunner

    return NerPipelineRunner(name="pylades_deduce", backend=DeduceBackend(name_fallback=False))


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


@pytest.mark.parametrize("record_id", _PINNED_NORMAL_LEAK_RECORDS)
def test_pinned_synthetic_normal_records_have_no_direct_leaks(
    record_id: str,
    synthetic_records_by_id: dict[str, EvalRecord],
    deduce_runner,
) -> None:
    record = synthetic_records_by_id[record_id]
    assert record.difficulty == "normal", f"{record_id} moet normal blijven in de gepinde set"
    report = evaluate([record], deduce_runner, warmup=False)
    leaks = report["leaks"]
    assert leaks["leak_rate"] == 0.0, (
        f"{record_id}: direct-identifier-lek: {leaks['items']}"
    )


@pytest.mark.parametrize("record_id", _PINNED_ADVERSARIAL_LEAK_RECORDS)
def test_pinned_synthetic_adversarial_records_have_no_direct_leaks_without_gliner(
    record_id: str,
    synthetic_records_by_id: dict[str, EvalRecord],
    deduce_runner,
) -> None:
    record = synthetic_records_by_id[record_id]
    assert record.difficulty == "adversarial"
    report = evaluate([record], deduce_runner, warmup=False)
    leaks = report["leaks"]
    assert leaks["leak_rate"] == 0.0, (
        f"{record_id}: direct-identifier-lek (zonder GLiNER): {leaks['items']}"
    )


def test_synthetic_dataset_zero_direct_leaks_without_gliner(
    synthetic_records_by_id: dict[str, EvalRecord],
    deduce_runner,
) -> None:
    report = evaluate(list(synthetic_records_by_id.values()), deduce_runner, warmup=False)
    leaks = report["leaks"]
    assert leaks["leak_rate"] == 0.0, (
        f"Direct-identifier-lek zonder GLiNER: {leaks['direct_leaked']}/{leaks['direct_total']} "
        f"{leaks['items'][:10]}"
    )


def test_report_has_expected_structure(normal_report: dict) -> None:
    for key in ("scores", "confusion", "leaks", "exposure", "latency", "per_record", "performance"):
        assert key in normal_report
    assert "exact" in normal_report["scores"]
    assert "overlap" in normal_report["scores"]


def test_full_dataset_runs_and_reports_known_gaps() -> None:
    # De volledige set (incl. adversarial) moet draaien en de bekende
    # clinical-gap (DIAGNOSIS zonder detector) als blootstelling rapporteren.
    report = evaluate(build_records(), PyladesPipelineRunner())
    clinical = report["exposure"]["clinical_sensitive"]
    assert clinical["exposed"] >= 1


class _CountingRunner:
    """Stub-runner die het aantal aanroepen telt en een vaste latency teruggeeft."""

    name = "counting"

    def __init__(self, latency_ms: float = 5.0) -> None:
        self.calls = 0
        self._latency_ms = latency_ms

    def run(self, prompt: str) -> RunOutput:
        self.calls += 1
        # Eerste aanroep (warm-up) krijgt een hoge cold-start-latency; deze mag
        # niet in de gemeten percentielen terechtkomen.
        latency = 1000.0 if self.calls == 1 else self._latency_ms
        return RunOutput(predicted=[], outbound_text=prompt, latency_ms=latency)


def test_warmup_runs_extra_call_and_excludes_cold_start() -> None:
    records = [r for r in build_records() if r.difficulty == "normal"]
    runner = _CountingRunner()
    report = evaluate(records, runner, warmup=True)

    assert runner.calls == len(records) + 1
    assert report["latency"]["warmup"] is True
    assert report["latency"]["warmup_ms"] == 1000.0
    # De cold-start (1000 ms) zit niet in de steady-state-percentielen.
    assert report["latency"]["p95_ms"] == 5.0


def test_no_warmup_includes_cold_start() -> None:
    records = [r for r in build_records() if r.difficulty == "normal"]
    runner = _CountingRunner()
    report = evaluate(records, runner, warmup=False)

    assert runner.calls == len(records)
    assert report["latency"]["warmup"] is False
    assert report["latency"]["warmup_ms"] is None
    # Zonder warm-up zit de cold-start (1000 ms) in de meting: p95 ligt boven de
    # steady-state (5 ms) en de max-latency wordt meegenomen.
    assert report["latency"]["p95_ms"] > 5.0


def test_eval_progress_callback_reports_warmup_and_records() -> None:
    records = [r for r in build_records() if r.difficulty == "normal"][:2]
    seen: list[tuple[int, int, str]] = []

    def _track(current: int, total: int, label: str) -> None:
        seen.append((current, total, label))

    evaluate(records, _CountingRunner(), warmup=True, on_progress=_track)
    assert seen[0] == (0, len(records), "warm-up")
    assert seen[1:] == [(1, len(records), records[0].id), (2, len(records), records[1].id)]
