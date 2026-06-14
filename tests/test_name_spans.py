"""Tests voor NAME-span-uitbreiding (tussenvoegsels + aanspreekvormen)."""

from __future__ import annotations

import pytest

from proxy.name_spans import expand_name_span, expand_name_span_left


def _expand(text: str, name: str) -> str:
    start = text.index(name)
    end = start + len(name)
    new_start, new_end = expand_name_span(text, start, end)
    return text[new_start:new_end]


def test_expands_de_tussenvoegsel() -> None:
    text = "verwijzing door huisarts De Smid, praktijk te Olst"
    assert _expand(text, "Smid") == "De Smid"


def test_expands_de_heer_honorific() -> None:
    text = "Bij de heer Jansen is sprake van een carcinoom."
    assert _expand(text, "Jansen") == "de heer Jansen"


def test_expands_dr_prefix() -> None:
    text = "Overleg met dr. Nguyen over behandeling."
    assert _expand(text, "Nguyen") == "dr. Nguyen"


def test_expands_apostrophe_suffix() -> None:
    text = "Diëtiste mevrouw N'Diaye adviseerde natriumbeperking."
    assert _expand(text, "N") == "mevrouw N'Diaye"
    assert _expand(text, "mevrouw N") == "mevrouw N'Diaye"


def test_expands_mevrouw() -> None:
    text = "Contact opgenomen met mevrouw De Boer."
    assert _expand(text, "De Boer") == "mevrouw De Boer"


def test_expands_van_de_particle_chain() -> None:
    text = "Partner van de Vries meldde zich."
    assert _expand(text, "Vries") == "van de Vries"


def test_no_expansion_without_prefix() -> None:
    text = "Patiënt Janssen werd opgenomen."
    assert _expand(text, "Janssen") == "Janssen"


def test_no_expansion_mid_word() -> None:
    text = "Code Smid wordt niet uitgebreid."
    start = text.index("Smid")
    new_start, new_end = expand_name_span_left(text, start, start + 4)
    assert (new_start, new_end) == (start, start + 4)


@pytest.mark.parametrize(
    ("text", "core", "expected"),
    [
        ("arts-assistent De Slager noteerde", "Slager", "De Slager"),
        ("Verwijzer: huisarts De Smid, praktijk", "Smid", "De Smid"),
        ("Meneer Pietersen meldde", "Pietersen", "Meneer Pietersen"),
    ],
)
def test_expansion_cases(text: str, core: str, expected: str) -> None:
    assert _expand(text, core) == expected
