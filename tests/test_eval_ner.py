"""Tests voor de laag-2 NER-adapters en het vergelijkend rapport (TESTPLAN fase 3).

We testen met gestubde backends (geen GLiNER/DEDUCE/trf-download nodig):
- de NerPipelineRunner-merge (regex wint bij overlap, laag-labels kloppen);
- de label-mapping van de GLiNER- en DEDUCE-adapters;
- dat het rapport laag 2 correct beschrijft;
- het vergelijkend rapport (CSV/HTML).
"""

from __future__ import annotations

import sys

import pytest

from eval.compare import _dig, write_comparison
from eval.metrics.generalization import format_generalization_summary
from eval.evaluate import describe_layers, evaluate
from eval.runners.ner_backends import (
    DeduceBackend,
    GlinerBackend,
    NerBackendError,
    NerSpan,
    SpacyNerBackend,
    _gliner_windows,
    _merge_ner_spans,
)
from proxy.deduce_layer import _deduce_base_tag
from eval.runners.ner_pipeline import NerPipelineRunner
from eval.schema import EvalRecord, GoldEntity
from shared.models import EntityType

# Elfproef-geldig fictief BSN (zelfde als de warm-up gebruikt).
_BSN = "123456782"


class _FakeNer:
    """Laag-2-backend die vaste substrings op de doeltypes mapt."""

    name = "fake"
    layer = "fake"
    desc = "fake (test)"

    def __init__(self, items: list[tuple[str, EntityType]]) -> None:
        self.items = items

    def ensure_available(self) -> None:
        return None

    def detect(self, text: str) -> list[NerSpan]:
        spans: list[NerSpan] = []
        for sub, etype in self.items:
            i = text.find(sub)
            if i < 0:
                continue
            spans.append(NerSpan(i, i + len(sub), sub, etype, 0.9))
        return spans


def test_runner_merges_regex_and_ner_regex_wins_overlap() -> None:
    prompt = f"Jan de Vries, BSN {_BSN}."
    # ORG-span overlapt het BSN-bereik en moet sneuvelen (regex wint).
    backend = _FakeNer([("Jan de Vries", EntityType.NAME), (_BSN, EntityType.ORG)])
    runner = NerPipelineRunner(name="t", backend=backend)

    out = runner.run(prompt)
    by_type = {(p.type, p.layer) for p in out.predicted}

    assert (EntityType.BSN, "regex") in by_type
    assert (EntityType.NAME, "fake") in by_type
    # De overlappende ORG-voorspelling is gedropt.
    assert all(p.type is not EntityType.ORG for p in out.predicted)
    # Outbound maskeert zowel de naam als het BSN.
    assert "Jan de Vries" not in out.outbound_text
    assert _BSN not in out.outbound_text


def test_runner_exposes_layer2_metadata() -> None:
    runner = NerPipelineRunner(name="t", backend=_FakeNer([]))
    assert runner.layer2_desc == "fake (test)"
    assert runner.spacy_model is None
    assert runner.use_llm is False


def test_spacy_backend_sets_model_name_on_runner() -> None:
    backend = SpacyNerBackend("nl_core_news_lg")
    assert backend.layer == "spacy"
    assert backend.name == "spacy_lg"
    assert backend.desc == "spacy (nl_core_news_lg)"


def test_report_describes_layer2_from_runner() -> None:
    prompt = "Patiënt Jan de Vries is opgenomen."
    name_start = prompt.index("Jan de Vries")
    record = EvalRecord(
        id="r1",
        prompt=prompt,
        entities=[
            GoldEntity(
                start=name_start,
                end=name_start + len("Jan de Vries"),
                text="Jan de Vries",
                type=EntityType.NAME,
            )
        ],
    )
    runner = NerPipelineRunner(name="t", backend=_FakeNer([("Jan de Vries", EntityType.NAME)]))
    report = evaluate([record], runner, warmup=False)

    assert report["layers_config"]["layer2"] == "fake (test)"
    assert report["run_meta"]["layer2"] == "fake (test)"
    assert report["layers_config"]["layer3"] == "niet gedraaid (laag 3 uit)"


def test_describe_layers_falls_back_to_deduce() -> None:
    layers = describe_layers({"use_llm": False})
    assert layers["layer2"] == "deduce (NL-medisch + rol-NAME-heuristiek)"


def test_spacy_backend_unavailable_raises_with_hint() -> None:
    backend = SpacyNerBackend("nl_core_news_does_not_exist")
    with pytest.raises(NerBackendError, match="spacy download"):
        backend.ensure_available()


def test_spacy_trf_unavailable_explains_no_dutch_trf() -> None:
    backend = SpacyNerBackend("nl_core_news_trf")
    with pytest.raises(NerBackendError, match="geen Nederlands transformer"):
        backend.ensure_available()


# --- GLiNER-adapter -------------------------------------------------------


class _FakeGlinerModel:
    def predict_entities(self, text: str, labels: list[str], threshold: float) -> list[dict]:
        return [
            {"start": 0, "end": 3, "text": "Jan", "label": "person", "score": 0.95},
            {"start": 5, "end": 8, "text": "OLV", "label": "organization", "score": 0.8},
            {"start": 9, "end": 12, "text": "xyz", "label": "vehicle", "score": 0.9},
        ]


def test_gliner_backend_maps_and_filters_labels() -> None:
    backend = GlinerBackend()
    backend._model = _FakeGlinerModel()  # ensure_available keert vroeg terug
    spans = backend.detect("Jan, OLV, xyz")
    types = [s.type for s in spans]
    assert types == [EntityType.NAME, EntityType.ORG]  # 'vehicle' gefilterd


def test_gliner_windows_single_and_overlapping() -> None:
    short = _gliner_windows("abc", limit=10, overlap=2)
    assert short == [(0, "abc")]
    long_text = "x" * 25
    windows = _gliner_windows(long_text, limit=10, overlap=3)
    assert windows[0] == (0, "x" * 10)
    assert windows[1][0] == 7  # stride = 10 - 3
    assert windows[-1][1] == long_text[windows[-1][0] :]


def test_merge_ner_spans_keeps_higher_score_on_overlap() -> None:
    low = NerSpan(0, 5, "Jan", EntityType.NAME, 0.6)
    high = NerSpan(1, 4, "an", EntityType.NAME, 0.9)
    merged = _merge_ner_spans([low, high])
    assert len(merged) == 1
    assert merged[0].score == 0.9


class _ChunkedFakeGlinerModel:
    """Geeft een entity terug op het einde van elk chunk-argument."""

    def predict_entities(self, text: str, labels: list[str], threshold: float) -> list[dict]:
        end = len(text)
        start = max(0, end - 4)
        return [
            {
                "start": start,
                "end": end,
                "text": text[start:end],
                "label": "person",
                "score": 0.8,
            }
        ]


def test_gliner_detect_chunked_maps_global_offsets() -> None:
    # limit=10 → meerdere chunks; entity aan chunk-einde moet globale offset krijgen.
    backend = GlinerBackend(max_length=3)  # char_limit ≈ 9
    backend._model = _ChunkedFakeGlinerModel()
    text = "a" * 22
    spans = backend.detect(text)
    assert spans
    assert all(s.end <= len(text) for s in spans)
    assert any(s.start > 0 for s in spans)


def test_gliner_loads_with_max_length(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int | None] = {}

    class _FakeGLiNER:
        @classmethod
        def from_pretrained(
            cls, model_name: str, *, max_length: int | None = None, **kwargs: object
        ):
            captured["max_length"] = max_length
            return _FakeGlinerModel()

    fake_gliner = type(sys)("gliner")
    fake_gliner.GLiNER = _FakeGLiNER  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner", fake_gliner)
    backend = GlinerBackend(max_length=768)
    backend.ensure_available()
    assert captured["max_length"] == 768


# --- DEDUCE-adapter -------------------------------------------------------


class _Ann:
    def __init__(self, start: int, end: int, text: str, tag: str) -> None:
        self.start_char = start
        self.end_char = end
        self.text = text
        self.tag = tag


class _Doc:
    def __init__(self, annotations: list[_Ann]) -> None:
        self.annotations = annotations


class _FakeDeduce:
    def __init__(self, anns: list[_Ann]) -> None:
        self._anns = anns

    def deidentify(self, text: str) -> _Doc:
        return _Doc(self._anns)


def test_deduce_base_tag() -> None:
    assert _deduce_base_tag("persoon+initiaal") == "persoon"
    assert _deduce_base_tag("patient_naam") == "patient"
    assert _deduce_base_tag("LOCATIE") == "locatie"


def test_deduce_backend_maps_and_filters_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    from proxy import deduce_layer

    monkeypatch.setattr(
        deduce_layer,
        "_get_deduce",
        lambda: _FakeDeduce(
            [
                _Ann(0, 3, "Jan", "patient"),
                _Ann(5, 8, "OLV", "instelling"),
                _Ann(9, 19, "01-01-1980", "datum"),
            ]
        ),
    )
    spans = DeduceBackend(name_fallback=False).detect("ignored")
    assert [s.type for s in spans] == [EntityType.NAME, EntityType.ORG]
    assert all(s.score == 1.0 for s in spans)


# --- Vergelijkend rapport -------------------------------------------------


def _mini_report(runner: str, layer2: str, micro: float, leak: int) -> dict:
    gen = {
        "checked": 10,
        "ok": 10 - leak,
        "rate": (10 - leak) / 10,
        "failures": [],
    }
    gen["summary"] = format_generalization_summary(gen)
    return {
        "runner": runner,
        "generated_at": "2026-06-11T17:00:00",
        "environment": {"summary": "TestMac", "python": "3.11.0"},
        "layers_config": {"layer2": layer2},
        "totals": {"records": 10},
        "scores": {
            "exact": {"micro": {"f1": micro}},
            "overlap": {"micro": {"f1": micro}, "macro_f1": micro},
        },
        "performance": {
            "direct_identifier_macro_f1": micro,
            "indirect_identifier_macro_f1": micro,
        },
        "leaks": {"direct_leaked": leak, "direct_total": 20, "leak_rate": leak / 20},
        "over_redaction": 0,
        "generalization": gen,
        "latency": {"p50_ms": 12.0, "p95_ms": 34.0},
    }


def test_dig_reads_nested_and_missing() -> None:
    report = _mini_report("pylades_deduce_runtime", "deduce (NL-medisch)", 0.9, 1)
    assert _dig(report, ("scores", "overlap", "macro_f1")) == 0.9
    assert _dig(report, ("nope", "nope")) == ""


def test_write_comparison_outputs_csv_and_html(tmp_path) -> None:
    reports = [
        _mini_report("pylades_deduce_runtime", "deduce (NL-medisch + rol-NAME-heuristiek)", 0.80, 2),
        _mini_report("pylades_gliner", "gliner (pii)", 0.88, 0),
    ]
    runtime_html = tmp_path / "pylades_deduce_runtime-20260101-120000.html"
    gliner_html = tmp_path / "pylades_gliner-20260101-120100.html"
    runtime_html.write_text("<html></html>", encoding="utf-8")
    gliner_html.write_text("<html></html>", encoding="utf-8")
    paths = write_comparison(
        reports,
        tmp_path,
        runner_html={
            "pylades_deduce_runtime": runtime_html,
            "pylades_gliner": gliner_html,
        },
    )

    csv_text = paths["csv"].read_text(encoding="utf-8")
    assert "generalisatie BR-B" in csv_text
    assert "pylades_deduce_runtime" in csv_text and "pylades_gliner" in csv_text
    assert csv_text.strip().count("\n") == 2  # header + 2 rijen

    html = paths["html"].read_text(encoding="utf-8")
    assert "NER-vergelijking" in html
    assert "deduce (NL-medisch + rol-NAME-heuristiek)" in html
    assert "gliner (pii)" in html
    assert f'<a href="{runtime_html.name}">pylades_deduce_runtime</a>' in html
    assert f'<a href="{gliner_html.name}">pylades_gliner</a>' in html
    assert "Span-matching" in html
    assert "generalisatie BR-B" in html
    assert "F1 (exact)" in html and "F1 (overlap)" in html
