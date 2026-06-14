"""Tests voor context-gebaseerde NAME-detectie (zorgrollen/relaties)."""

from __future__ import annotations

from proxy.role_names import detect_role_context_name_spans


def _names(text: str) -> list[str]:
    return [surface for _, _, surface in detect_role_context_name_spans(text)]


def test_label_line_specialist() -> None:
    text = "Verpleegkundig specialist: Okonkwo\nAnamnese:"
    assert _names(text) == ["Okonkwo"]


def test_label_line_referring_gp() -> None:
    text = "Verwijzend huisarts: Okonkwo, praktijk te Kampen"
    assert _names(text) == ["Okonkwo"]


def test_role_before_name_consultant() -> None:
    text = "Medebehandeling door internist dr. Visser en consulent diabetologie Okonkwo."
    assert _names(text) == ["Okonkwo"]


def test_relation_partner_two_word_name() -> None:
    text = "Woont samen met partner Olufemi Adeyemi. Dochter komt langs."
    assert _names(text) == ["Olufemi Adeyemi"]


def test_huisarts_de_surname() -> None:
    text = "Verwezen door huisarts De Dokter (praktijk centrum)."
    assert _names(text) == ["De Dokter"]


def test_profession_surname_dokter() -> None:
    text = "Patiënt werd eerder behandeld door Dokter, cardioloog, in 2019."
    assert _names(text) == ["Dokter"]


def test_iom_abbreviation() -> None:
    text = "Bijstellen metformine dosering i.o.m. Okonkwo\n\nLaboratorium"
    assert _names(text) == ["Okonkwo"]


def test_physician_assistant_without_colon() -> None:
    text = "specialist Okonkwo en physician assistant Andersson. Tolk ingeschakeld."
    assert "Andersson" in _names(text)


def test_relation_comma_mevrouw() -> None:
    text = "Patiënt woont samen met echtgenote, mevrouw Koningin, en wordt ondersteund."
    assert _names(text) == ["Koningin"]


def test_relation_comma_dochter() -> None:
    text = "Patiënte werd opgehaald door haar dochter, Petrova, met de auto."
    assert _names(text) == ["Petrova"]


def test_fysiotherapie_door_name() -> None:
    text = "Diëtist Timmerman consulteerde. Fysiotherapie door Mwangi opgestart."
    assert _names(text) == ["Mwangi"]


def test_referral_dietist_name() -> None:
    text = "Controle over 6 weken. Verwijzing diëtist Mwangi voor natriumbeperkt dieet."
    assert _names(text) == ["Mwangi"]


def test_arts_assistent_label_with_colon() -> None:
    text = "Arts-assistent: Fernández\nGecontroleerd door dr. Slager."
    assert _names(text) == ["Fernández"]


def test_unicode_arts_assistent() -> None:
    text = "Verslag opgesteld door arts-assistent Đorđević, gecontroleerd door dr. Slager."
    assert _names(text) == ["Đorđević"]


def test_arts_assistent() -> None:
    text = "Opgesteld door arts-assistent Mwangi, geautoriseerd door dr. Slager."
    assert _names(text) == ["Mwangi"]
