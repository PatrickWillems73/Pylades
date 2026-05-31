# Beveiligingsbeleid

Pylades verwerkt privacygevoelige scenario's. We nemen beveiligingsmeldingen
serieus en waarderen het als je een kwetsbaarheid verantwoord meldt.

## Wat onder scope valt

- De proxy (`proxy/`), gedeelde modules (`shared/`) en de pseudonimiserings-,
  vault- en audit-logica.
- De Streamlit-UI (`ui/`) voor zover het lekken van gevoelige data of
  toegangsproblemen betreft.

Buiten scope: de bekende, expliciet gedocumenteerde v0.3-beperkingen (zie
[README.md](README.md) → "Bekende v0.3-beperking" en "Productie-disclaimer").
Pylades is een proof of concept en **niet** productie-geschikt voor echte
zorgdata; meldingen die alleen die bekende beperkingen herhalen, beschouwen we
niet als nieuwe kwetsbaarheid.

## Hoe je een kwetsbaarheid meldt

- **Niet** via een openbaar issue, pull request of discussie.
- Gebruik bij voorkeur GitHub's **private vulnerability reporting** via het
  tabblad **Security → Report a vulnerability** van deze repository.
- Lukt dat niet, neem dan via GitHub contact op met de oprichters
  (Siebrand Zoethout, Patrick Willems).

Vermeld in je melding:

- Een beschrijving van het probleem en de impact.
- Reproductiestappen of een proof of concept.
- Betrokken versie/commit en omgeving.

**Belangrijk:** voeg **nooit** echte persoonsgegevens of patiëntdata toe aan een
melding. Gebruik uitsluitend fictieve voorbeelden (zie [STYLE.md](STYLE.md)).

## Wat je van ons mag verwachten

- We bevestigen ontvangst zo snel als redelijkerwijs mogelijk.
- We beoordelen de melding, houden je op de hoogte van de status en stemmen
  een verantwoord openbaarmakingsmoment met je af.
- We streven ernaar bevestigde kwetsbaarheden in een passende release op te
  lossen en de melder te erkennen, tenzij je anoniem wilt blijven.

Dit is een vrijwillig beheerd project zonder commerciële SLA; reactietijden
zijn een inspanningsverplichting, geen garantie.
