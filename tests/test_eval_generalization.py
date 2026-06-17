"""Tests voor generalisatie-scoring in het eval-harnas."""

from __future__ import annotations

from eval.evaluate import evaluate
from eval.generators.bootstrap import build_records
from eval.metrics.generalization import format_generalization_summary, generalization_failures
from eval.runners.base import PredEntity, RunOutput
from eval.runners.pylades_pipeline import PyladesPipelineRunner
from eval.schema import EvalRecord
from shared.models import EntityType


def test_generalization_ok_on_bootstrap_patient() -> None:
    record = next(r for r in build_records() if r.id == "basic_patient")
    runner = PyladesPipelineRunner()
    out = runner.run(record.prompt)
    assert generalization_failures(record, out.predicted) == []


def test_generalization_failure_when_entity_missing() -> None:
    record = next(r for r in build_records() if r.id == "basic_patient")
    failures = generalization_failures(record, [])
    assert len(failures) == len(record.expected_generalization)
    assert all(item["reason"] == "entity_not_detected" for item in failures)


def test_generalization_failure_wrong_entity_type() -> None:
    record = EvalRecord(
        id="gen_type",
        prompt="Thoraxfoto op 28-05-2019.",
        entities=[
            {
                "start": 14,
                "end": 24,
                "text": "28-05-2019",
                "type": EntityType.EXAM_DATE,
                "category": "quasi_identifier",
            }
        ],
        expected_generalization={"28-05-2019": "2019-05"},
    )
    pred = PredEntity(
        start=14,
        end=24,
        text="28-05-2019",
        type=EntityType.BIRTHDATE,
        confidence=1.0,
        layer="regex",
    )
    failures = generalization_failures(record, [pred])
    assert len(failures) == 1
    assert failures[0]["reason"] == "wrong_entity_type"
    assert failures[0]["detected_type"] == "birthdate"
    assert failures[0]["expected_type"] == "exam_date"


def test_evaluate_report_includes_generalization_block() -> None:
    records = [r for r in build_records() if r.expected_generalization][:3]
    report = evaluate(records, PyladesPipelineRunner(), warmup=False)
    gen = report["generalization"]
    assert gen["checked"] > 0
    assert gen["ok"] == gen["checked"]
    assert gen["rate"] == 1.0
    assert gen["failures"] == []
    assert gen["summary"] == format_generalization_summary(gen)


def test_format_generalization_summary_always_shows_rate() -> None:
    assert format_generalization_summary({"checked": 0, "ok": 0, "rate": 1.0}) == "0/0 (n.v.t.)"
    assert format_generalization_summary({"checked": 4, "ok": 4, "rate": 1.0}) == "4/4 (100,0%)"
    assert format_generalization_summary({"checked": 4, "ok": 3, "rate": 0.75}) == "3/4 (75,0%)"


def test_evaluate_generalization_with_detected_postcode() -> None:
    record = EvalRecord(
        id="gen_ok",
        prompt="Postcode 7411AB in Deventer.",
        entities=[],
        expected_generalization={"7411AB": "74"},
    )
    pred = PredEntity(
        start=9,
        end=15,
        text="7411AB",
        type=EntityType.POSTCODE_PC6,
        confidence=1.0,
        layer="regex",
    )

    class _StubRunner:
        name = "stub"

        def run(self, prompt: str) -> RunOutput:
            return RunOutput(predicted=[pred], outbound_text="", latency_ms=1.0)

    report = evaluate([record], _StubRunner(), warmup=False)
    assert report["generalization"]["checked"] == 1
    assert report["generalization"]["ok"] == 1
    assert report["generalization"]["summary"] == "1/1 (100,0%)"
