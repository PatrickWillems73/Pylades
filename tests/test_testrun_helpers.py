"""Tests voor `ui/testrun_helpers.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from proxy.mapping import export_mappings_csv
from proxy.review import decide, enqueue, get_pending
from shared.config import settings
from shared.db import init_databases
from shared.models import (
    DetectionLayer,
    Entity,
    EntityType,
    PseudonymizationMode,
    ReviewStatus,
    Template,
)
from ui.testrun_helpers import (
    AnalysisResult,
    PrivacyReportContext,
    analyze_prompt,
    build_privacy_report_csv,
    build_privacy_report_md,
    extract_assistant_text,
    fill_input,
    format_curl_equivalent,
    highlight_pairs,
    lay_explanation,
    pseudonymized_highlights,
    reconcile_analysis_for_display,
    response_signals,
    summarize_for_lay_user,
)


@pytest.fixture
def review_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


@pytest.fixture
def pylades_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def test_fill_input_substitutes_placeholder() -> None:
    out = fill_input("Vat samen: {input}", "Mevr. Pietersen, BSN 123456782.")
    assert out == "Vat samen: Mevr. Pietersen, BSN 123456782."


def test_fill_input_appends_when_placeholder_missing() -> None:
    # Tolerant pad voor preview op een (nog) niet-gevalideerde template.
    out = fill_input("Statische opdracht zonder placeholder.", "Dossiertekst.")
    assert out == "Statische opdracht zonder placeholder.\n\nDossiertekst."


def test_fill_input_returns_dossier_for_empty_template() -> None:
    assert fill_input("", "Alleen het dossier.") == "Alleen het dossier."


def test_fill_input_tolerates_stray_braces() -> None:
    # `str.format` zou hier knallen op de losse `{` — onze helper niet.
    out = fill_input("Functie f(x) { return {input}; } // einde", "Anna")
    assert out == "Functie f(x) { return Anna; } // einde"


def _basic_template() -> Template:
    return Template(
        id=1,
        groep="test",
        naam="basic",
        llm_provider="anthropic",
        llm_naam="claude-3-haiku",
        prompt_tekst="Anonimiseer en vat samen: {input}",
    )


def test_analyze_prompt_detects_and_pseudonymizes_without_vault_writes(
    pylades_env: None,
) -> None:
    template = _basic_template()
    dossier = "Patiënt Pietersen, BSN 123456782, woont op 1011AB."

    result = analyze_prompt(template, dossier)

    assert dossier in result.original
    assert result.pseudonymized != result.original
    types = {e.entity_type for e in result.entities}
    assert EntityType.BSN in types
    assert all(e.pseudonym for e in result.entities)
    assert all(e.effective_mode is PseudonymizationMode.ONE_WAY for e in result.entities)

    csv_text = export_mappings_csv()
    # Alleen header; geen rijen (dry-run schrijft niet naar de vault).
    assert csv_text.strip().splitlines() == [
        "session_id,pseudonym,original,entity_type,entity_category,pseudonymization_mode,created_at"
    ]


def test_analyze_prompt_returns_pending_when_confidence_low(pylades_env: None) -> None:
    """Een gewone tekst zonder rare data moet de helper gewoon laten slagen."""
    template = _basic_template()
    result = analyze_prompt(template, "Voorbeeld zonder rare data.")
    assert isinstance(result.entities, list)
    assert isinstance(result.pending_review, list)


# ---------------------------------------------------------------------------
# Lay-summary + per-entiteit-uitleg
# ---------------------------------------------------------------------------


def _make_entity(
    *,
    original: str = "Pietersen",
    entity_type: EntityType = EntityType.NAME,
    mode: PseudonymizationMode | None = PseudonymizationMode.ONE_WAY,
    pseudonym: str | None = "[PER-abc]",
    confidence: float = 0.95,
) -> Entity:
    return Entity(
        original=original,
        entity_type=entity_type,
        confidence=confidence,
        detection_layer=DetectionLayer.REGEX,
        start=0,
        end=len(original),
        pseudonym=pseudonym,
        effective_mode=mode,
    )


def _make_analysis(
    entities: list[Entity] | None = None,
    pending: list[Entity] | None = None,
    *,
    generalized: str = "gen",
) -> AnalysisResult:
    return AnalysisResult(
        session_id="abc123",
        original="orig",
        generalized=generalized,
        pseudonymized="pseudo",
        entities=entities or [],
        pending_review=pending or [],
    )


def test_reconcile_syncs_pending_from_review_queue(review_env: None) -> None:
    template = _basic_template()
    stale_pending = _make_entity(original="De Boer", pseudonym=None)
    dry = _make_analysis(pending=[stale_pending])
    enqueue("sess-sync", "context met De Boer", [stale_pending])

    synced = reconcile_analysis_for_display(
        dry,
        template,
        proxy_session_id="sess-sync",
        session_resolved=False,
        response_status=423,
    )

    assert len(synced.pending_review) == 1
    assert synced.pending_review[0].original == "De Boer"
    assert synced.session_id == "sess-sync"


def test_reconcile_moves_accepted_into_protected_after_resolve(review_env: None) -> None:
    template = _basic_template()
    pending_ent = _make_entity(original="De Boer", pseudonym=None)
    dry = _make_analysis(
        entities=[_make_entity(original="123456782", entity_type=EntityType.BSN)],
        pending=[pending_ent],
        generalized="Patiënt De Boer, BSN 123456782.",
    )
    enqueue("sess-done", "context", [pending_ent])
    item = get_pending("sess-done")[0]
    assert item.id is not None
    decide(item.id, ReviewStatus.ACCEPTED)

    synced = reconcile_analysis_for_display(
        dry,
        template,
        proxy_session_id="sess-done",
        session_resolved=True,
        response_status=423,
    )

    assert synced.pending_review == []
    originals = {e.original for e in synced.entities}
    assert "123456782" in originals
    assert "De Boer" in originals
    assert all(e.pseudonym for e in synced.entities)


def test_reconcile_without_proxy_session_leaves_dry_run(review_env: None) -> None:
    template = _basic_template()
    dry = _make_analysis(pending=[_make_entity(pseudonym=None)])

    assert (
        reconcile_analysis_for_display(
            dry,
            template,
            proxy_session_id=None,
            session_resolved=False,
            response_status=None,
        )
        is dry
    )


def test_summarize_after_resolved_423_shows_handled_not_waiting() -> None:
    result = _make_analysis()
    summary = summarize_for_lay_user(result, response_status=423, session_resolved=True)
    assert "wacht op beslissingen" not in summary.summary_line
    assert "beslissingen afgehandeld" in summary.summary_line


def test_summarize_for_lay_user_counts_modes() -> None:
    result = _make_analysis(
        entities=[
            _make_entity(mode=PseudonymizationMode.ONE_WAY),
            _make_entity(mode=PseudonymizationMode.TWO_WAY),
            _make_entity(mode=PseudonymizationMode.ONE_WAY),
        ],
        pending=[_make_entity(pseudonym=None)],
    )
    summary = summarize_for_lay_user(result, response_status=200)

    assert summary.protected_count == 3
    assert summary.two_way_count == 1
    assert summary.pending_count == 1
    assert "antwoord ontvangen" in summary.summary_line
    assert "3 gegevens beschermd" in summary.summary_line
    assert "1 naam vertaald terug" in summary.summary_line


def test_summarize_for_lay_user_omits_optional_phrases() -> None:
    result = _make_analysis(entities=[_make_entity()])
    summary = summarize_for_lay_user(result)

    assert summary.protected_count == 1
    assert summary.two_way_count == 0
    assert summary.pending_count == 0
    assert "antwoord ontvangen" not in summary.summary_line
    assert "vertaald terug" not in summary.summary_line
    assert "wacht" not in summary.summary_line


def test_summarize_for_lay_user_status_phrases() -> None:
    result = _make_analysis(entities=[_make_entity()])
    assert "wacht op beslissingen" in summarize_for_lay_user(
        result, response_status=423
    ).summary_line
    assert "geen verbinding" in summarize_for_lay_user(
        result, response_status=0
    ).summary_line
    assert "fout bij het externe LLM" in summarize_for_lay_user(
        result, response_status=503
    ).summary_line


def test_lay_explanation_per_mode() -> None:
    one_way = _make_entity(mode=PseudonymizationMode.ONE_WAY)
    two_way = _make_entity(mode=PseudonymizationMode.TWO_WAY)
    pending = _make_entity(pseudonym=None, mode=None)

    assert "alleen een code" in lay_explanation(one_way)
    assert "teruggezet naar het origineel" in lay_explanation(two_way)
    assert "Wacht op jouw beoordeling" in lay_explanation(pending, pending=True)


# ---------------------------------------------------------------------------
# Response-signals
# ---------------------------------------------------------------------------


def test_extract_assistant_text_basic() -> None:
    payload = {
        "content": [
            {"type": "text", "text": "Hallo wereld."},
            {"type": "text", "text": " Tweede zin."},
        ],
    }
    assert extract_assistant_text(payload) == "Hallo wereld.\n Tweede zin."


def test_extract_assistant_text_handles_non_dict() -> None:
    assert extract_assistant_text("not-a-dict") == ""
    assert extract_assistant_text({"raw": "x"}) == ""
    assert extract_assistant_text({"content": "nope"}) == ""


def test_response_signals_happy_path() -> None:
    payload = {
        "content": [{"type": "text", "text": "Hi"}],
        "stop_reason": "end_turn",
    }
    sig = response_signals(200, payload)
    assert sig.ok is True
    assert sig.status == 200
    assert sig.assistant_text == "Hi"
    assert sig.is_truncated is False
    assert sig.is_refusal is False
    assert sig.is_empty is False


def test_response_signals_truncated() -> None:
    sig = response_signals(
        200,
        {"content": [{"type": "text", "text": "Lange tekst…"}], "stop_reason": "max_tokens"},
    )
    assert sig.is_truncated is True
    # ok blijft True; truncatie is een waarschuwing maar geen fout.
    assert sig.ok is True


def test_response_signals_refusal_is_not_ok() -> None:
    sig = response_signals(
        200,
        {"content": [{"type": "text", "text": ""}], "stop_reason": "refusal"},
    )
    assert sig.is_refusal is True
    assert sig.ok is False


def test_response_signals_empty_text_is_not_ok() -> None:
    sig = response_signals(200, {"content": [{"type": "text", "text": "   "}]})
    assert sig.is_empty is True
    assert sig.ok is False


def test_response_signals_extracts_error_messages() -> None:
    sig = response_signals(401, {"error": {"message": "missing api key"}})
    assert sig.ok is False
    assert sig.error_message == "missing api key"

    sig2 = response_signals(404, {"detail": "Template bestaat niet"})
    assert sig2.error_message == "Template bestaat niet"


# ---------------------------------------------------------------------------
# Curl-equivalent
# ---------------------------------------------------------------------------


def test_format_curl_equivalent_includes_template_and_dossier() -> None:
    cmd = format_curl_equivalent(
        template_id=7,
        dossier="Mevr. Pietersen",
        proxy_port=8080,
    )
    assert "http://127.0.0.1:8080/v1/messages" in cmd
    assert '"template_id": 7' in cmd
    assert '"dossier": "Mevr. Pietersen"' in cmd
    assert "resume_session" not in cmd


def test_format_curl_equivalent_includes_resume_when_set() -> None:
    cmd = format_curl_equivalent(
        template_id=1,
        dossier="x",
        proxy_port=8080,
        resume_session="deadbeef",
    )
    assert '"resume_session": "deadbeef"' in cmd


# ---------------------------------------------------------------------------
# Privacy-rapport
# ---------------------------------------------------------------------------


def _context() -> PrivacyReportContext:
    return PrivacyReportContext(
        template_naam="demo",
        template_groep="zorg",
        session_id="sess-1",
        response_status=200,
        response_stop_reason="end_turn",
        two_way_justification="Naam mag terug in de dialoog.",
    )


def test_privacy_report_md_contains_summary_table_and_justification() -> None:
    result = _make_analysis(
        entities=[
            _make_entity(original="Pietersen", entity_type=EntityType.NAME),
            _make_entity(
                original="123456782",
                entity_type=EntityType.BSN,
                pseudonym="[BSN-abc]",
                mode=PseudonymizationMode.TWO_WAY,
            ),
        ],
        pending=[_make_entity(pseudonym=None, original="?", mode=None)],
    )
    md = build_privacy_report_md(result, _context())

    assert "# Pylades — privacy-rapport" in md
    assert "zorg / demo" in md
    assert "`sess-1`" in md
    assert "HTTP 200" in md
    assert "Pietersen" in md
    assert "123456782" in md
    assert "## Wacht op jouw beoordeling" in md
    assert "TWO_WAY-onderbouwing" in md


def test_privacy_report_csv_has_stable_header_and_rows() -> None:
    result = _make_analysis(
        entities=[_make_entity(original="Pietersen")],
        pending=[_make_entity(original="?", pseudonym=None, mode=None)],
    )
    csv_text = build_privacy_report_csv(result, _context())
    lines = csv_text.strip().splitlines()
    assert lines[0] == (
        "session_id,original,type,category,treatment,two_way,"
        "pseudonym,confidence,detection_layer,pending_review"
    )
    # 1 entity + 1 pending row.
    assert len(lines) == 3
    assert "Pietersen" in lines[1]
    assert lines[2].endswith(",true")


# ---------------------------------------------------------------------------
# Highlight-pairs
# ---------------------------------------------------------------------------


def test_highlight_pairs_marks_pending_distinctly() -> None:
    result = _make_analysis(
        entities=[
            _make_entity(original="Anna", mode=PseudonymizationMode.ONE_WAY),
            _make_entity(original="123456782", mode=PseudonymizationMode.TWO_WAY),
        ],
        pending=[_make_entity(original="??", pseudonym=None, mode=None)],
    )
    originals, generalized, pseudos = highlight_pairs(result)
    keys_by_needle = dict(originals)
    assert keys_by_needle["Anna"] == "one_way"
    assert keys_by_needle["123456782"] == "two_way"
    assert keys_by_needle["??"] == "pending"
    # Generalized + pseudoniem-lijsten bevatten geen pending-items.
    assert all(k != "pending" for _, k in generalized)
    assert all(k != "pending" for _, k in pseudos)


def test_pseudonymized_highlights_includes_placeholders_and_pending() -> None:
    result = _make_analysis(
        entities=[
            _make_entity(
                original="Anna",
                pseudonym="PAT-001",
                mode=PseudonymizationMode.ONE_WAY,
            ),
            _make_entity(
                original="123456782",
                pseudonym="BSN-001",
                mode=PseudonymizationMode.TWO_WAY,
            ),
        ],
        pending=[_make_entity(original="Twijfel", pseudonym=None, mode=None)],
    )
    highlights = dict(pseudonymized_highlights(result))
    assert highlights["PAT-001"] == "one_way"
    assert highlights["BSN-001"] == "two_way"
    assert highlights["Twijfel"] == "pending"
