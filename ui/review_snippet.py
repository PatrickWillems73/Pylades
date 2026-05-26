"""Pure context-snippet-helper voor de Review-queue-pagina.

Aparte module zodat de Streamlit-pagina niets test-onvriendelijks importeert
en de snippet-logica met `pytest` valideerbaar blijft zonder runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True, slots=True)
class ContextSnippet:
    """Drie segmenten rondom een gedetecteerde span.

    Splitsen we doelbewust in `before`/`match`/`after` (in plaats van één
    string met markdown-highlight) zodat de UI vrij is in opmaak en
    eenvoudige string-asserts werken in tests.
    """

    before: str
    match: str
    after: str
    truncated_before: bool
    truncated_after: bool


def make_context_snippet(
    original_text: str,
    detected_text: str,
    *,
    words: int = 5,
) -> ContextSnippet:
    """Trek `words` woorden vóór en na de eerste hit van `detected_text`.

    Vinden we `detected_text` niet (bijv. door whitespace-normalisatie of
    omdat de UI per ongeluk verkeerde input meegaf), dan vallen we terug
    op een snippet met enkel `match=detected_text` en een lege context;
    raisen zou een hele pagina blokkeren op één kapot item — onwenselijk
    voor een review-queue waar opgevoerde items juist beoordeeld moeten
    worden.
    """
    if words < 0:
        raise ValueError("words moet >= 0 zijn")

    idx = original_text.find(detected_text)
    if idx < 0:
        return ContextSnippet(
            before="",
            match=detected_text,
            after="",
            truncated_before=False,
            truncated_after=False,
        )

    before_raw = original_text[:idx]
    after_raw = original_text[idx + len(detected_text) :]

    before_words = _WORD_RE.findall(before_raw)
    after_words = _WORD_RE.findall(after_raw)

    truncated_before = len(before_words) > words
    truncated_after = len(after_words) > words

    before = " ".join(before_words[-words:]) if words else ""
    after = " ".join(after_words[:words]) if words else ""

    return ContextSnippet(
        before=before,
        match=detected_text,
        after=after,
        truncated_before=truncated_before,
        truncated_after=truncated_after,
    )
