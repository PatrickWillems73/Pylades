"""Pragmatische set van zeldzame ICD-10 codes (BR-B05).

V0.3 heeft geen volledige prevalentie-database; deze ~30 codes zijn handmatig
gecureerd op basis van Orphanet-richtlijnen (<1:10.000 of <1:2.000 met
klinische ernst). v1.0 vervangt dit door officiële RIVM/CBS-data of een
prevalentie-API.

`set` (niet `list` of `frozenset`): de enige operatie is membership-check
`code in RARE_ICD10_CODES`, die wil je O(1). `frozenset` zou theoretisch
correcter zijn (constante data), maar `set` leest natuurlijker en de
constante wordt op module-niveau toch niet opnieuw toegewezen — geen
praktisch verschil.
"""

import re
from typing import Final

# ICD-10 format: één letter A-T of V-Z (U is gereserveerd), twee cijfers,
# optioneel een punt + 1-2 cijfers. Zelfde alfabet als de regex in
# proxy/detection.py zodat een match in de detector ook hier kan matchen.
_ICD10_FORMAT: Final[re.Pattern[str]] = re.compile(r"^[A-TV-Z]\d{2}(\.\d{1,2})?$")


RARE_ICD10_CODES: Final[set[str]] = {
    # Hematologische maligniteiten (zeldzame subtypes)
    "C82.0",  # Folliculair lymfoom graad I
    "C84.4",  # Perifeer T-cel lymfoom NOS
    "C84.5",  # Andere mature T-/NK-cel lymfomen
    "C91.0",  # Acute lymfoblastaire leukemie
    "C92.4",  # Acute promyelocyt leukemie (APL)
    "C92.5",  # Acute myelomonocyt leukemie
    # Erfelijke metabole aandoeningen
    "E70.0",  # Klassieke fenylketonurie
    "E70.2",  # Tyrosinemie
    "E71.0",  # Ahornsiroop-urineziekte (MSUD)
    "E72.1",  # Stoornissen zwavelhoudende aminozuren (homocystinurie)
    "E74.0",  # Glycogeenstapelingsziekte
    "E75.0",  # GM2-gangliosidose (Tay-Sachs)
    "E75.2",  # Andere sfingolipidoses (Gaucher, Niemann-Pick)
    "E76.0",  # Mucopolysaccharidose type I (Hurler)
    "E76.1",  # Mucopolysaccharidose type II (Hunter)
    "E76.2",  # Andere mucopolysaccharidoses
    "E83.0",  # Ziekte van Wilson
    # Neuromusculaire aandoeningen
    "G11.1",  # Cerebellaire ataxie met vroege aanvang
    "G11.4",  # Hereditaire spastische paraplegie
    "G12.0",  # Infantiele spinale spieratrofie type I (Werdnig-Hoffmann)
    "G12.1",  # Andere hereditaire spinale spieratrofie
    "G71.0",  # Spierdystrofie (Duchenne, Becker, faciale-scapula-humerale)
    "G71.1",  # Myotone aandoeningen (myotone dystrofie)
    "G71.2",  # Congenitale myopathieën
    # Primaire immuundeficiënties
    "D80.0",  # Hereditaire hypogammaglobulinemie
    "D81.0",  # SCID met reticulaire dysgenesie
    "D81.1",  # SCID met laag T- en B-celaantal
    # Pediatrische / neurologische zeldzame
    "F84.2",  # Syndroom van Rett
    "Q78.0",  # Osteogenesis imperfecta
}


def _check_codes_format() -> None:
    # Import-time fail-fast: een typo in een code (bv. ontbrekende punt of
    # verkeerde letter) wordt nu gevangen, niet pas wanneer een fixture-test
    # halverwege detection.py een onmogelijke match zoekt.
    invalid = [code for code in RARE_ICD10_CODES if not _ICD10_FORMAT.match(code)]
    if invalid:
        raise RuntimeError(f"Ongeldige ICD-10 codes in RARE_ICD10_CODES: {sorted(invalid)}")


_check_codes_format()
