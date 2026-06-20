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
    assert set(_names(text)) == {"Visser", "Okonkwo"}


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
    assert _names(text) == ["Timmerman", "Mwangi"]


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
    assert _names(text) == ["Bakker", "Joana"]


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


def test_polikliniek_bij_mukherjee() -> None:
    text = "Controle over twee weken op de polikliniek bij Mukherjee. Ontslagbrief verzonden."
    assert _names(text) == ["Mukherjee"]


def test_cc_naar_oyelaran() -> None:
    text = "Ontslagbrief verzonden naar verwijzend arts en cc naar Oyelaran."
    assert _names(text) == ["Oyelaran"]


def test_verwijzer_huisarts_de_groot() -> None:
    text = "Verwijzer: huisarts J. de Groot, Huisartsenpraktijk Centrum, Houten"
    assert _names(text) == ["huisarts J. de Groot"]


def test_dr_role_paren_al_rashidi() -> None:
    text = "Aanwezig: dr. Beekman (oncoloog), dr. Al-Rashidi (radiotherapeut), dr. Vermeulen"
    assert "Al-Rashidi" in _names(text)


def test_dr_adviseert_al_rashidi() -> None:
    text = "Dr. Al-Rashidi adviseert neoadjuvante chemoradiatie."
    assert _names(text) == ["Al-Rashidi"]


def test_dash_name_comma_role_tilanus() -> None:
    text = "- De Wit, verpleegkundig specialist\n- Tilanus, patholoog\n"
    assert "Tilanus" in _names(text)


def test_role_paren_name_tilanus() -> None:
    text = "Beoordeling patholoog (Tilanus): adenocarcinoom."
    assert _names(text) == ["Tilanus"]


def test_dash_role_label_smit() -> None:
    text = "- verpleegkundig specialist L. Smit\n"
    assert _names(text) == ["verpleegkundig specialist L. Smit"]


def test_opgesteld_door_verpleegkundig_specialist_smit() -> None:
    text = "Verslag opgesteld door verpleegkundig specialist L. Smit, Maasstad Ziekenhuis."
    assert _names(text) == ["verpleegkundig specialist L. Smit"]


def test_holdout_leak_kopie_aan_de_jong() -> None:
    text = "Kopie aan:\nHuisarts dr. M. de Jong\nDorpsstraat 45"
    assert _names(text) == ["dr. M. de Jong"]


def test_holdout_leak_verstuurd_huisartsenpraktijk() -> None:
    text = "Verstuurd aan huisarts: huisartsenpraktijk De Esdoorn, Esdoornstraat 14"
    assert _names(text) == ["huisartsenpraktijk De Esdoorn"]


def test_holdout_leak_naar_fysiotherapeut() -> None:
    text = "verstuurd naar huisarts Okonkwo en naar fysiotherapeut Timmerman van FysioCentrum."
    assert set(_names(text)) == {"Okonkwo", "Timmerman"}


def test_holdout_leak_ingeschakeld_via_mwangi() -> None:
    text = "Fysiotherapie ingeschakeld via Mwangi."
    assert _names(text) == ["Mwangi"]


def test_holdout_leak_dietist_slowinski() -> None:
    text = "consult door verpleegkundig specialist Koning en diëtist Słowiński."
    assert _names(text) == ["Koning", "Słowiński"]


def test_holdout_leak_contactpersoon_schoonzoon() -> None:
    text = "Contactpersoon: schoonzoon Okonkwo, bereikbaar via 06-79931223."
    assert _names(text) == ["Okonkwo"]


def test_holdout_leak_fysiotherapie_bij() -> None:
    text = "Patiënte krijgt fysiotherapie bij Schipper aan de Brinkgreverweg."
    assert _names(text) == ["Schipper"]


def test_holdout_leak_wijkverpleegkundige_mol() -> None:
    text = "Nahuiscontrole door wijkverpleegkundige Mol van Zorggroep IJssel."
    assert _names(text) == ["Mol"]


def test_holdout_leak_dietist_fernandez() -> None:
    text = "Diëtist: Fernández\nVoedingsadvies verstrekt door Fernández."
    assert _names(text) == ["Fernández", "Fernández"]


def test_holdout_leak_fysiotherapie_ingezet_door() -> None:
    text = "Fysiotherapie ingezet door Haddad."
    assert _names(text) == ["Haddad"]


def test_holdout_leak_oncoloog_dr_al_rashid() -> None:
    text = "MDO met chirurg Timmerman en oncoloog dr. Al-Rashid."
    assert _names(text) == ["Al-Rashid"]


def test_holdout_leak_triagist_okafor() -> None:
    text = "advies gegeven door triagist Okafor."
    assert _names(text) == ["Okafor"]


def test_holdout_leak_coassistent_aanwezig() -> None:
    text = "Co-assistent aanwezig: Kovács."
    assert _names(text) == ["Kovács"]


def test_holdout_leak_eerdergenoemde_okonkwo() -> None:
    text = "dochter mevrouw Tanaka en eerdergenoemde Okonkwo."
    assert set(_names(text)) == {"Okonkwo", "Tanaka"}


def test_holdout_leak_dossier_voornaam() -> None:
    text = "Patiënt: Bakker, voornaam Wilhelmina\nGeboortedatum: 26-08-2004"
    assert _names(text) == ["Bakker", "Wilhelmina"]
