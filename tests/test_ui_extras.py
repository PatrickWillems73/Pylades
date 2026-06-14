"""Tests voor `ui/ui_extras.py` helpers."""

from __future__ import annotations

from ui.ui_extras import (
    llm_markdown_to_docs_clipboard_html,
    llm_markdown_to_html,
    normalize_llm_markdown,
)


def test_normalize_llm_markdown_converts_unicode_bullets() -> None:
    src = "Intro\n• Eerste\n  • Genest"
    out = normalize_llm_markdown(src)
    assert "•" not in out
    assert "- Eerste" in out
    assert "  - Genest" in out


def test_normalize_llm_markdown_tightens_spaced_list_items() -> None:
    src = "# Titel\n\n• een\n\n• twee\n\n\n• drie"
    out = normalize_llm_markdown(src)
    assert "- een\n- twee\n- drie" in out
    html_out = llm_markdown_to_html(src)
    assert "<li>\n<p>" not in html_out
    assert "<li>een</li>" in html_out


def test_normalize_llm_markdown_collapses_triple_blank_lines() -> None:
    out = normalize_llm_markdown("A\n\n\n\nB")
    assert out == "A\n\nB"


def test_llm_markdown_to_html_renders_headings_and_lists() -> None:
    src = "# Samenvatting\n\n## Deel\n\n- item één\n\n**vet**"
    html_out = llm_markdown_to_html(src)
    assert "<h1>Samenvatting</h1>" in html_out
    assert "<h2>Deel</h2>" in html_out
    assert "<li>item één</li>" in html_out
    assert "<strong>vet</strong>" in html_out


def test_llm_markdown_to_html_escapes_raw_html() -> None:
    html_out = llm_markdown_to_html("<script>x</script>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_docs_clipboard_html_uses_styled_headings_not_h_tags() -> None:
    src = "# Titel\n\n## Deel\n\n- item"
    docs_html = llm_markdown_to_docs_clipboard_html(src)
    assert "<h1>" not in docs_html
    assert "<h2>" not in docs_html
    assert "font-size:17pt" in docs_html
    assert "font-size:15pt" in docs_html
    assert "StartFragment" in docs_html
    assert "Helvetica,Arial,sans-serif" in docs_html


def test_docs_clipboard_html_preserves_utf8_characters() -> None:
    src = "# Patiëntdossier\n\nOverzicht voor patiënt."
    docs_html = llm_markdown_to_docs_clipboard_html(src)
    assert "Patiëntdossier" in docs_html
    assert "patiënt" in docs_html
    assert "Ã«" not in docs_html
