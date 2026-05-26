"""Tests voor `ui/review_snippet.py`."""

from __future__ import annotations

import pytest

from ui.review_snippet import make_context_snippet


def test_basic_5_word_window() -> None:
    text = "een twee drie vier vijf zes ZEVEN acht negen tien elf twaalf"
    snip = make_context_snippet(text, "ZEVEN", words=5)
    assert snip.match == "ZEVEN"
    assert snip.before == "twee drie vier vijf zes"
    assert snip.after == "acht negen tien elf twaalf"
    assert snip.truncated_before is True
    assert snip.truncated_after is False


def test_truncation_flags_on_both_sides() -> None:
    text = "a b c d e f g h MATCH i j k l m n o p"
    snip = make_context_snippet(text, "MATCH", words=5)
    assert snip.before == "d e f g h"
    assert snip.after == "i j k l m"
    assert snip.truncated_before is True
    assert snip.truncated_after is True


def test_short_context_not_truncated() -> None:
    text = "korte aanloop MATCH einde"
    snip = make_context_snippet(text, "MATCH", words=5)
    assert snip.before == "korte aanloop"
    assert snip.after == "einde"
    assert snip.truncated_before is False
    assert snip.truncated_after is False


def test_match_not_found_returns_safe_fallback() -> None:
    snip = make_context_snippet("irrelevant", "ZOEKWOORD", words=5)
    assert snip.match == "ZOEKWOORD"
    assert snip.before == ""
    assert snip.after == ""
    assert snip.truncated_before is False
    assert snip.truncated_after is False


def test_zero_words_returns_empty_context() -> None:
    snip = make_context_snippet("links MATCH rechts", "MATCH", words=0)
    assert snip.before == ""
    assert snip.after == ""
    # Linker- en rechterkant bestaan wél in de tekst, dus truncated=True
    assert snip.truncated_before is True
    assert snip.truncated_after is True


def test_negative_words_raises() -> None:
    with pytest.raises(ValueError, match="words"):
        make_context_snippet("x MATCH y", "MATCH", words=-1)


def test_match_at_start_of_text() -> None:
    snip = make_context_snippet("MATCH rest van zin", "MATCH", words=5)
    assert snip.before == ""
    assert snip.after == "rest van zin"
    assert snip.truncated_before is False


def test_first_occurrence_wins() -> None:
    text = "voor MATCH midden MATCH eind"
    snip = make_context_snippet(text, "MATCH", words=2)
    assert snip.before == "voor"
    assert snip.after == "midden MATCH"
