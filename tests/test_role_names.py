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


def test_markdown_co_assistent_label() -> None:
    text = "Gelre Ziekenhuizen. **Arts-assistent:** Okonkwo.\n\nOpgenomen op"
    assert _names(text) == ["Okonkwo"]


def test_co_assistent_hyphen_label() -> None:
    text = "Huisarts Visser, praktijk te Twello\nCo-assistent: Okonkwo\n\nOpgenomen op"
    assert "Okonkwo" in _names(text)


def test_dossier_header_patient_surname() -> None:
    text = "**KLINISCH DOSSIER — INTERNE GENEESKUNDE**\n\nPatiënt: Bakker, voornaam Joana"
    assert _names(text) == ["Bakker"]


def test_dossier_header_patientnaam() -> None:
    text = "**Patiëntnaam:** Karim El Idrissi\nGeboortedatum: 04-02-1982"
    assert _names(text) == ["Karim El Idrissi"]


def test_notitie_verpleegkundige_obrien() -> None:
    text = "tot 30 maart. **Notitie verpleegkundige O'Brien:** patiënte verstond instructies."
    assert _names(text) == ["O'Brien"]


def test_consult_door_fernandez() -> None:
    text = "Goede diurese. Consult diëtetiek door Fernández ivm natriumbeperking."
    assert _names(text) == ["Fernández"]


def test_by_collega_mukherjee() -> None:
    text = "Fysiotherapie ingezet door collega Mukherjee. Beloop op 17 maart."
    assert _names(text) == ["Mukherjee"]


def test_verpleegkundige_werkwoord() -> None:
    text = "Naar afdeling B3. Verpleegkundige Okonkwo verzorgde de dagrapportage."
    assert _names(text) == ["Okonkwo"]


def test_internist_castro_without_dr() -> None:
    text = "Brief verzonden naar verwijzer en aan internist Castro voor overdracht."
    assert _names(text) == ["Castro"]


def test_dochter_three_word_name() -> None:
    text = "Dochter Aïsha El Amrani is eerste contactpersoon en bereikbaar."
    assert _names(text) == ["Aïsha El Amrani"]


def test_dietiste_yamamoto_adviseerde() -> None:
    text = "Diëtiste Yamamoto adviseerde natriumbeperking."
    assert _names(text) == ["Yamamoto"]


def test_consult_longarts_mukherjee() -> None:
    text = "Consult longarts Mukherjee aangevraagd. Insulineschema bijgesteld."
    assert _names(text) == ["Mukherjee"]


def test_medeoverleg_longarts_okafor() -> None:
    text = "Medeoverleg met longarts Okafor en physician assistant Bos."
    assert _names(text) == ["Okafor", "Bos"]


def test_mantelzorger_bakr() -> None:
    text = "Brief verzonden naar huisarts Dr. Visser en naar mantelzorger Bakr."
    assert _names(text) == ["Bakr"]


def test_geboortenaam_fernandez() -> None:
    text = "Lucía (zie ook geboortenaam Fernández) Geboortedatum: 08-09-1947"
    assert _names(text) == ["Fernández"]


def test_label_name_not_greedy_opgenomen() -> None:
    text = "Verpleegkundig specialist: Fernández  Opgenomen op 19-08-2025"
    assert _names(text) == ["Fernández"]
