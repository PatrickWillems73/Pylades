"""Regressietests voor het uitgebreide rapportformaat (context, groepering, p-kolom)."""

from __future__ import annotations

from pathlib import Path

from eval.evaluate import describe_layers, evaluate
from eval.report import _group_of, _nl_num, _totals_row, format_run_summary, write_html
from eval.runners.base import PredEntity, RunOutput
from eval.schema import EvalRecord
from shared.models import DetectionLayer, EntityType


class _StubRunner:
    """Minimale runner: één voorspelling, geen DEDUCE-load nodig (snel)."""

    name = "stub"
    use_llm = False
    layer2_desc = "deduce (NL-medisch + rol-NAME-heuristiek)"
    spacy_model = None
    llm_model = "qwen3:1.7b"

    def run(self, prompt: str) -> RunOutput:  # noqa: ARG002
        return RunOutput(
            predicted=[
                PredEntity(
                    text="Jan",
                    type=EntityType.NAME,
                    start=0,
                    end=3,
                    confidence=0.99,
                    layer=DetectionLayer.DEDUCE.value,
                )
            ],
            outbound_text="",
            latency_ms=1.0,
            llm_status="disabled",
        )


def _record() -> EvalRecord:
    return EvalRecord(
        id="r1",
        prompt="Jan",
        scenario="t",
        difficulty="normal",
        seed=0,
        entities=[],
    )


def test_report_has_context_and_prediction_blocks() -> None:
    report = evaluate([_record()], _StubRunner(), warmup=False)
    assert "environment" in report
    assert "summary" in report["environment"]
    assert report["layers_config"]["layer1"] == "regex"
    assert report["layers_config"]["layer2"] == "deduce (NL-medisch + rol-NAME-heuristiek)"
    assert report["predicted_by_type"].get("name") == 1


def test_describe_layers_reflects_llm_status() -> None:
    on_ok = describe_layers({"use_llm": True, "llm_model": "m", "llm_status": "ok"})
    assert on_ok["layer3"] == "m"
    down = describe_layers({"use_llm": True, "llm_model": "m", "llm_status": "unavailable"})
    assert "niet beschikbaar" in down["layer3"]
    off = describe_layers({"use_llm": False})
    assert "niet gedraaid" in off["layer3"]


def test_html_has_groups_subtotals_and_ordered_leaklist(tmp_path: Path) -> None:
    report = evaluate([_record()], _StubRunner(), warmup=False)
    html = write_html(report, tmp_path / "r.html").read_text(encoding="utf-8")

    assert "Context van de testrun" in html
    assert "<ol>" in html and "<ul>" not in html
    assert "<th>p</th>" in html
    assert "Pylades Testharnas run" in html
    assert "direct-identifiers geleakt" in html
    # Groepen in de juiste volgorde + som-rijen per groep + grand total.
    direct_idx = html.index("Direct-identifier")
    indirect_idx = html.index("Indirect-identifier")
    assert direct_idx < indirect_idx
    assert html.count("Som / Gemiddelde — ") == 2
    assert "Σ" not in html
    assert "Generaal totaal" in html
    # Foot-note definieert span-matching, micro/macro-F1 en leak-niet-herleidbaarheid.
    assert "Span-matching" in html
    assert "F1 (exact)" in html or "Exact</strong>" in html
    assert "overlap" in html.lower()
    assert "micro-F1" in html and "macro-F1" in html
    assert "niet</em> uit tp/fp/fn af te leiden" in html
    assert "Performance van de testrun" in html
    assert "Direct-identifier macro-F1" in html
    assert "Indirect-identifier macro-F1" in html
    assert "Generalisatie (BR-B01..B05)" in html


def test_format_run_summary_includes_performance_block() -> None:
    report = evaluate([_record()], _StubRunner(), warmup=False)
    summary = format_run_summary(report)
    assert "Runner: stub · records: 1" in summary
    assert "gold-entities:" in summary
    assert "direct-identifiers:" in summary
    assert "Performance van de testrun" in summary
    assert "Direct-identifier macro-F1:" in summary
    assert "Indirect-identifier macro-F1:" in summary
    assert "Latency mean:" in summary
    assert "Generalisatie BR-B:" in summary


def test_totals_row_sums_counts_and_averages_gold_rows_only() -> None:
    # p/tp/fp/fn worden opgeteld; P/R/F1 zijn het gemiddelde over de rijen mét
    # gold (tp+fn>0). De nul-rij telt niet mee in dat gemiddelde.
    def score(tp: int, fp: int, fn: int, f1: float) -> dict[str, float]:
        return {"tp": tp, "fp": fp, "fn": fn, "precision": f1, "recall": f1, "f1": f1}

    entries = [
        ("a", "regex", 5, score(4, 0, 0, 1.0)),
        ("b", "regex", 5, score(2, 0, 2, 0.5)),
        ("c", "regex", 0, score(0, 0, 0, 0.0)),  # nul-rij → uitgesloten
    ]
    row = _totals_row("Som / Gemiddelde — test", entries, background="#fff")
    assert "<td>10</td>" in row  # p = 5 + 5 + 0
    assert "<td>6</td>" in row  # tp = 4 + 2
    assert "0,75" in row  # gemiddelde F1 over 2 gold-rijen: (1,0 + 0,5) / 2


def test_html_uses_dutch_number_format() -> None:
    assert _nl_num(1218) == "1.218"
    assert _nl_num(0.9091) == "0,9091"
    assert _nl_num(1234.5) == "1.234,5"
    assert _nl_num(15) == "15"


def test_direct_vs_indirect_grouping_matches_categories() -> None:
    # NAME is direct; ORG (quasi) en PRODUCT (free_text) vallen onder indirect.
    assert _group_of(EntityType.NAME.value) == "direct"
    assert _group_of(EntityType.ORG.value) == "indirect"
    assert _group_of(EntityType.PRODUCT.value) == "indirect"
