# Pylades — specificatie v0.3

> **Let op — implementatie is op punten geëvolueerd t.o.v. deze spec.**
> Dit document is de oorspronkelijke v0.3-specificatie. De gebouwde software
> wijkt op enkele punten af; raadpleeg voor het *definitieve* gedrag steeds
> [PLAN.md](PLAN.md) (§15a) en [README.md](README.md). Bekende afwijkingen:
> - UI-pagina's leven in `ui/views/` (entry `ui/Home.py`); "Templates" heet
>   nu **Opdrachten** en "Testruns" is opgegaan in de **Home**-pagina, met een
>   aparte **Status**-pagina.
> - Het hervat-pad gebruikt het **body-veld `resume_session`** in plaats van
>   de header `X-Pylades-Resume-Session` (zie PLAN §15a).
> - Detectielaag 2 draait runtime op **DEDUCE** (`DetectionLayer.DEDUCE`);
>   spaCy is alleen eval/benchmark (`--extra eval`). De `threshold_spacy_*`-
>   config-keys blijven als legacy-namen in gebruik voor NAME/ORG/LOCATION.
> - De proxy heeft extra modules naast deze spec: `templates.py` (CRUD),
>   `deduce_layer.py` (laag 2) en `name_spans.py` + `role_names.py`
>   (NL-naam/rol-heuristiek); `shared/` heeft daarnaast `version.py`.
> - `Template` heeft drie extra velden: `max_tokens`, `use_llm` en
>   `sort_order` (zie PLAN §15a).
> - De vault-tabel `mappings` gebruikt `UNIQUE(session_id, original,
>   entity_type)` en heeft een extra kolom `generalized_to`.

---

## Wat we bouwen

**Pylades** — een lokale tool die opdrachten pseudonimiseert vóór verzending naar
een extern LLM en de response (afhankelijk van modus) de-pseudonimiseert bij
terugkomst. Naam is hommage aan trappistenbier; net als de monniken houdt
Pylades zaken intern die niet naar buiten horen.

### Versies
- **v0.3**: lokale persoonlijke POC; dit document beschrijft v0.3
- **v1.0**: productie-versie met multi-tenancy, provider-agnostisch, encryptie
  at rest, rol-gebaseerde autorisatie, HSM-sleutelbeheer — buiten scope van
  v0.3; alleen vermeld als roadmap-aanduiding

### Architectuur op één regel
Twee processen die naast elkaar draaien op `localhost`, met twee gescheiden
SQLite-databases voor defense-in-depth tegen runtime-exfiltratie.

1. **FastAPI proxy** op `localhost:8080` — luistert op het pad
   `POST /v1/messages` met een eigen body-contract (geen Anthropic-pass-through),
   pseudonimiseert/de-pseudonimiseert en stuurt upstream naar Anthropic.
2. **Streamlit UI** op `localhost:8501` — beheer van opdracht-templates,
   testruns met side-by-side view, manual review queue, audit log, configuratie.

Twee SQLite-databases (zie BR-G02):
- `pylades-content.db` — templates, audit_log, sessions, review_queue, config
- `pylades-vault.db` — mappings (pseudoniem ↔ original)

### Use case
Een Nederlandse gebruiker wil opdrachten met gevoelige data veilig naar een
extern LLM kunnen sturen zonder dat die data herleidbaar bij de LLM-provider
terechtkomt.

### Productie-disclaimer (verplicht in README.md)

Pylades v0.3 implementeert 12 specifieke business rules uit een
zorg-georiënteerde functionele specificatie, maar is **niet productie-geschikt
voor zorgdata**. Ontbrekende productie-vereisten zijn onder andere:

- Medisch NER-model (BR-A03)
- K-anonimiteit en l-diversity (BR-D-serie)
- DPA met LLM-aanbieder (BR-E-serie)
- TLS 1.3 met mTLS (BR-F01)
- Rol-gebaseerde autorisatie voor de-pseudonimisering (BR-H01)
- DPIA en FG-goedkeuring (BR-I-serie)
- HSM-sleutelbeheer (BR-C02)
- Tamper-evident logging (BR-G04)

Deze worden onderdeel van Pylades v1.0. Vóór elk productie-gebruik op
zorgdata: formele FG/DPO-toetsing.

### Bekende v0.3-beperking (verplicht in README.md)

De originele prompt wordt **plaintext** opgeslagen in `audit_log.original_prompt`
binnen `pylades-content.db`. Compromis van alleen content.db lekt daardoor de
oorspronkelijke gevoelige inhoud, zelfs zonder toegang tot de vault. De
mapping/content-separation (BR-G02) beschermt v0.3 daarom vooral tegen
**runtime-exfiltratie** (netwerk, provider-logs, geïntercepteerde responses),
niet tegen diefstal van de audit-database zelf. In v1.0 wordt
`original_prompt` óf encrypted-at-rest opgeslagen, óf vervangen door
`original_prompt_hash` voor integriteitsbewijs zonder inhoud.

---

## Scope v0.3

**In scope:**
- Persoonlijke tool op één machine (M1 MacBook Pro **8 GB**, macOS)
- Eén Anthropic API-key in `.env`
- Drie detectielagen: regex, DEDUCE 3.x (NL-medisch NER), lokaal LLM via Ollama
  (`qwen3:1.7b`) — laag 3 **standaard uit**, optioneel aan
- Pseudoniem-generatie via HMAC-SHA-256 (BR-C01)
- UI-pagina's: Home (testrun-flow), Status, Opdrachten (voorheen "Templates"),
  Review Queue, Audit, Config
- Generaliseringspipeline naast pseudonimisering (BR-B01-B05)
- Twee gescheiden SQLite-databases (BR-G02)
- **Super-default pseudonimiseringsmodus**: `one_way`
- **Per-template, per-EntityType override** van pseudonimiseringsmodus
- Eén-LLM-provider: Anthropic (Provider-agnostisch is v1.0)
- Fictieve Nederlandse testdata

**Onderdeel van v1.0 (niet bouwen in v0.3):**
- Multi-tenancy / multi-user
- Authenticatie of authorisatie op de UI
- Encryptie van data-at-rest
- Provider-agnostiek (OpenAI, Google, on-premise modellen)
- Productie-deployment, Docker, CI/CD
- Streaming responses
- Medisch NER (BR-A03), k-anonimiteit (BR-D), provider-DPA (BR-E),
  TLS-hardening (BR-F), governance-rules (BR-I, BR-J)

---

## Tech stack (vastgepind)

| Onderdeel | Keuze | Reden voor keuze |
|---|---|---|
| Python | 3.11+ | Match-statement, betere type hints |
| Package manager | `uv` | 10-100x sneller dan Poetry; modern standaard |
| Web framework (proxy) | `fastapi` >= 0.115 | Async, OpenAPI gratis |
| ASGI server | `uvicorn[standard]` | Standaard voor FastAPI |
| HTTP client | `httpx` >= 0.27 | Async, modern |
| UI framework | `streamlit` >= 1.40 | Pure Python, snelste tijd-tot-werkend |
| NER (laag 2) | `deduce` >= 3.0 | NL-medische NER; geen aparte modeldownload |
| Eval-vergelijking laag 2 | `spacy` + `nl_core_news_lg` (optioneel `--extra eval`) | Benchmark-only |
| Lokaal LLM (laag 3) | `ollama` Python library | `qwen3:1.7b` (1.4 GB); werkbaar op M1 8 GB |
| Database | `sqlite3` (stdlib) | v0.3-eenvoud; twee separate files voor BR-G02 |
| Settings | `pydantic-settings` v2 | Type-safe `.env`-loading; één bron voor config |
| Crypto | `hmac`, `hashlib`, `secrets` (stdlib) | HMAC-SHA-256 voor pseudoniemen (BR-C01) |

**Externe afhankelijkheden die de gebruiker zelf installeert:**
- `ollama serve` draaiend op `localhost:11434` (alleen bij actieve laag 3)
- Model `qwen3:1.7b` gepulld via `ollama pull` (~1.4 GB)
- DEDUCE wordt meegeïnstalleerd via `uv sync`; geen aparte download

---

## Bestandsstructuur

> Bijgewerkt naar de gebouwde structuur. De UI-pagina's leven onder
> `ui/views/` (entry `ui/Home.py`); de proxy/UI hebben extra hulpmodules
> die in de oorspronkelijke spec nog niet voorzien waren.

```
pylades/
├── pyproject.toml
├── README.md                       # incl. productie-disclaimer + v0.3-beperking
├── .env.example
├── .gitignore                      # incl. *.db, secrets/
├── secrets/
│   └── .gitkeep                    # bevat runtime de HMAC-sleutel
├── pylades-content.db               # runtime, niet in git
├── pylades-vault.db                 # runtime, niet in git
├── scripts/
│   └── pylades_services.py         # start/stop/restart proxy + UI
├── proxy/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app entrypoint
│   ├── detection.py                # drielaagse detectie (BR-A01, A02, A04)
│   ├── deduce_layer.py             # laag 2: DEDUCE NL-medische NER
│   ├── name_spans.py               # NL-naam-spanheuristiek
│   ├── role_names.py               # rol-/naam-heuristiek
│   ├── generalization.py           # generalisering (BR-B01-B05)
│   ├── pseudonymization.py         # HMAC-pseudoniemen (BR-C01, C06)
│   ├── mapping.py                  # PseudonymManager + vault-access (BR-G02)
│   ├── review.py                   # manual review queue (BR-A04)
│   ├── audit.py                    # content-logging (BR-G01)
│   └── templates.py                # template-CRUD (content-db)
├── ui/
│   ├── __init__.py
│   ├── Home.py                     # entry: bootstrap, navigatie, theme + testrun-flow
│   ├── app.py                      # compat-shim voor `streamlit run ui/app.py`
│   ├── status.py                   # status-checks (proxy/ollama/deduce/db)
│   ├── theme.py                    # kleur-/CSS-thema
│   ├── ui_extras.py                # gedeelde UI-shell (init_pylades_ui)
│   ├── navigation.py
│   ├── testrun_helpers.py          # fill_input + analyze_prompt
│   ├── audit_format.py
│   ├── review_flow.py / review_queue_helpers.py / review_snippet.py
│   ├── sidebar_state.py / cookies.py / favicon_sync.py
│   ├── assets/                     # logo.png, favicon.png
│   └── views/
│       ├── 0_Home.py
│       ├── 1_Status.py
│       ├── 2_Opdrachten.py         # voorheen "Templates"
│       ├── 3_Review_Queue.py
│       ├── 4_Audit.py
│       └── 5_Config.py
├── shared/
│   ├── __init__.py
│   ├── config.py                   # pydantic-settings
│   ├── db.py                       # twee connection helpers (content, vault)
│   ├── models.py                   # Pydantic datamodellen + Enums
│   ├── crypto.py                   # HMAC-helpers (BR-C01)
│   └── version.py                  # enige bron van waarheid voor de versie
├── data/
│   ├── __init__.py
│   ├── fixtures.py                 # fictieve testdata
│   └── icd10_rare.py               # set van zeldzame codes voor BR-B05
└── tests/                          # o.a. test_detection / _generalization /
    │                               # _pseudonymization / _mapping / _review /
    │                               # _db_separation / _proxy / _audit / _version
    └── __init__.py
```

---

## Architectuur en data flow

```
[Cursor / Python script / Claude Desktop]
            │ HTTPS (Pylades body-contract: template_id + dossier)
            ▼
[FastAPI proxy :8080]
   │
   ├─► Stage 1: DETECT (BR-A01, A02, A04)
   │     Regex laag    → BSN+elfproef, IBAN, PC6, telefoon, e-mail, MRN,
   │     │              EPD-id, kenteken, geboortedatum, projectcode
   │     DEDUCE laag     → NL-medische entiteiten, namen, locaties, orgs
   │     Qwen3 laag    → jargon, productnamen (standaard uit)
   │     ↓
   │     Classificatie: DIRECT_IDENTIFIER | QUASI_IDENTIFIER |
   │                    FREE_TEXT | CLINICAL_SENSITIVE
   │     ↓
   │     Confidence < threshold? → REVIEW QUEUE → pauze tot reviewed
   │
   ├─► Stage 2: GENERALIZE (BR-B01-B05)
   │     Geboortedatum  → geboortejaar
   │     Postcode (PC6) → PC2
   │     Leeftijd ≥90   → "90+"
   │     Datum opname   → maand-jaar
   │     Diagnose code  → flag voor review (BR-B05 lookup)
   │
   ├─► Stage 3: PSEUDONYMIZE (BR-C01, BR-C06)
   │     Per entity: bepaal effectieve modus (template-override of super-default)
   │     Pseudoniem = HMAC-SHA-256(session_key, original)[:6] hex
   │     session_key = HMAC-SHA-256(global_secret, session_id)
   │     Vault SQLite ── opslag mapping ALTIJD (BR-G02), zowel voor one_way
   │                     als two_way; verschil zit in response-fase
   │
   ├─► Content SQLite ── audit_log (volledig request) (BR-G01)
   │
   ├─► HTTPS POST → api.anthropic.com/v1/messages
   ├─► Response → de-pseudonimiseer ALLEEN entities met effectieve modus
   │              two_way; one_way entities blijven als [XXX-aaaaaa] staan
   └─► Eind: log volledig request+response in content.db

[Streamlit UI :8501]
   ├─► Templates      → CRUD + per-EntityType modus-override
   ├─► Testruns       → side-by-side preview + modus-indicator per entity
   ├─► Review Queue   → manual review van low-confidence detecties
   ├─► Audit          → recente requests met details
   └─► Config         → thresholds, super-default modus, entity-types,
                        generalisering aan/uit
```

---

## Business rules — implementatie

### BR-A01 — Classificatie in vier categorieën

**Regel.** Inkomende data classificeren in: `DIRECT_IDENTIFIER`,
`QUASI_IDENTIFIER`, `FREE_TEXT`, `CLINICAL_SENSITIVE`.

**Implementatie.** In `shared/models.py`:

```python
class EntityCategory(str, Enum):
    DIRECT_IDENTIFIER = "direct_identifier"
    QUASI_IDENTIFIER = "quasi_identifier"
    FREE_TEXT = "free_text"
    CLINICAL_SENSITIVE = "clinical_sensitive"

ENTITY_CATEGORY_MAP: dict[EntityType, EntityCategory] = {
    EntityType.BSN:               EntityCategory.DIRECT_IDENTIFIER,
    EntityType.NAME:              EntityCategory.DIRECT_IDENTIFIER,
    EntityType.EMAIL:             EntityCategory.DIRECT_IDENTIFIER,
    EntityType.PHONE:             EntityCategory.DIRECT_IDENTIFIER,
    EntityType.IBAN:              EntityCategory.DIRECT_IDENTIFIER,
    EntityType.POSTCODE_PC6:      EntityCategory.DIRECT_IDENTIFIER,
    EntityType.ADDRESS:           EntityCategory.DIRECT_IDENTIFIER,
    EntityType.MRN:               EntityCategory.DIRECT_IDENTIFIER,
    EntityType.EPD_ID:            EntityCategory.DIRECT_IDENTIFIER,
    EntityType.KENTEKEN:          EntityCategory.DIRECT_IDENTIFIER,
    EntityType.BIRTHDATE:         EntityCategory.DIRECT_IDENTIFIER,
    EntityType.POSTCODE_PC2:      EntityCategory.QUASI_IDENTIFIER,
    EntityType.BIRTH_YEAR:        EntityCategory.QUASI_IDENTIFIER,
    EntityType.AGE:               EntityCategory.QUASI_IDENTIFIER,
    EntityType.ORG:               EntityCategory.QUASI_IDENTIFIER,
    EntityType.LOCATION:          EntityCategory.QUASI_IDENTIFIER,
    EntityType.ADMISSION_DATE:    EntityCategory.QUASI_IDENTIFIER,
    EntityType.DISCHARGE_DATE:    EntityCategory.QUASI_IDENTIFIER,
    EntityType.EXAM_DATE:         EntityCategory.QUASI_IDENTIFIER,
    EntityType.ICD10_CODE:        EntityCategory.CLINICAL_SENSITIVE,
    EntityType.DIAGNOSIS:         EntityCategory.CLINICAL_SENSITIVE,
    EntityType.PRODUCT:           EntityCategory.FREE_TEXT,
    EntityType.PROJECT:           EntityCategory.FREE_TEXT,
}
```

**Acceptatiecriterium.** Elke `Entity` heeft een `category` veld dat
automatisch wordt afgeleid uit `entity_type` via deze mapping. Tests
verifiëren classificatie van BSN als `DIRECT_IDENTIFIER` en ICD10_CODE als
`CLINICAL_SENSITIVE`.

### BR-A02 — Direct-identifier detectie (NL-context)

**Regel.** Minimaal detecteren: BSN (met 11-test), naam, adres, postcode
(6-cijferig), telefoonnummer, e-mailadres, IBAN, geboortedatum, MRN,
EPD-id, foto, biometrisch, kenteken.

**Implementatie.** In `proxy/detection.py`:

```python
REGEX_PATTERNS: list[tuple[EntityType, re.Pattern, Callable | None]] = [
    (EntityType.EMAIL,        re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), None),
    (EntityType.IBAN,         re.compile(r"\bNL\d{2}[A-Z]{4}\d{10}\b"), validate_iban_checksum),
    (EntityType.BSN,          re.compile(r"\b\d{9}\b"), validate_bsn_elfproef),
    (EntityType.POSTCODE_PC6, re.compile(r"\b\d{4}\s?[A-Z]{2}\b"), None),
    (EntityType.PHONE,        re.compile(r"\b(?:\+31|0)[\s-]?[1-9](?:[\s-]?\d){8}\b"), None),
    (EntityType.KENTEKEN,     re.compile(r"\b[A-Z0-9]{1,3}-[A-Z0-9]{1,3}-[A-Z0-9]{1,3}\b"), None),
    (EntityType.BIRTHDATE,    re.compile(r"\b(0?[1-9]|[12]\d|3[01])[-/](0?[1-9]|1[0-2])[-/](19|20)\d{2}\b"), None),
    (EntityType.MRN,          re.compile(r"\bMRN[:\s-]?\d{6,10}\b", re.I), None),
    (EntityType.EPD_ID,       re.compile(r"\bEPD[:\s-]?\d{6,12}\b", re.I), None),
    (EntityType.ICD10_CODE,   re.compile(r"\b[A-TV-Z]\d{2}(\.\d{1,2})?\b"), None),
    (EntityType.PROJECT,      re.compile(r"\b[A-Z]{2,5}-\d{2,6}\b"), None),
]
```

**BSN elfproef validator:**

```python
def validate_bsn_elfproef(bsn: str) -> bool:
    """Valideer 9-cijferig BSN via elfproef.

    Rekenregel: som(d[i] * w[i]) waarbij weights = (9,8,7,6,5,4,3,2,-1).
    Resultaat moet deelbaar zijn door 11 EN niet 0 zijn.
    Belangrijk: BSN met leading zero behoudt 9-positie format door regex
    `\b\d{9}\b`, validator behandelt string-input dus leading zero blijft.
    """
    if len(bsn) != 9 or not bsn.isdigit():
        return False
    weights = (9, 8, 7, 6, 5, 4, 3, 2, -1)
    total = sum(int(d) * w for d, w in zip(bsn, weights, strict=True))
    return total != 0 and total % 11 == 0
```

**Onderdeel v1.0**: foto, biometrische detectie (vereist beeldherkenning).
Vermeld als ontbrekend in README onder "v1.0 roadmap".

**Acceptatiecriterium.** Test-fixtures met geldige BSN (`123456782`, valide
elfproef), ongeldige BSN (`123456789`), NL-IBAN, PC6, kenteken, MRN, EPD-id.
Geldige patronen → entity gedetecteerd; ongeldige patronen → niet gedetecteerd.

### BR-A04 — Configureerbare confidence-threshold + manual review queue

**Regel.** Threshold per detectiemodel configureerbaar; detecties onder
threshold gaan naar manual review queue voor verzending.

**Implementatie.** Nieuwe tabel in `pylades-content.db`:

```sql
CREATE TABLE review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    original_text TEXT NOT NULL,
    detected_text TEXT NOT NULL,
    proposed_entity_type TEXT NOT NULL,
    proposed_category TEXT NOT NULL,
    confidence REAL NOT NULL,
    detection_layer TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    user_decision_entity_type TEXT,
    user_decision_at TEXT,
    user_decision_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
```

Config-keys per laag (in `config` tabel):
- `threshold_regex` (default 1.0)
- `threshold_spacy_person` (default 0.85)
- `threshold_spacy_org` (default 0.80)
- `threshold_spacy_location` (default 0.85)
- `threshold_llm` (default 0.70)

In `proxy/detection.py`:

```python
def detect_with_review_routing(text: str) -> DetectionResult:
    """Detecteer entities; items onder threshold → review queue.

    Returns DetectionResult met:
    - confident_entities: list[Entity]  → kunnen direct doorstromen
    - pending_review: list[Entity]      → geblokkeerd tot reviewed
    """
```

In `proxy/main.py`: als `pending_review` niet leeg → return HTTP 423 (Locked)
met `{"session_id": ..., "review_url": ...}`.

UI Review Queue:
- Tabel met pending items, context-snippet (5 woorden vooraf/achterna)
- Acties: Accept / Reject / Change type (dropdown)
- Optionele note
- Knop "Hervat sessie" als alle items resolved → herhaal call via proxy met
  het body-veld `resume_session: <id>` (verouderd: header
  `X-Pylades-Resume-Session`; zie PLAN §15a)

**Acceptatiecriterium.** Detectie met confidence onder threshold plaatst entity
in review_queue met status `PENDING`. UI-actie verandert status; volgende
proxy-call voor die sessie pakt accepteerde types op.

### BR-B01 — Geboortedata → geboortejaar

**Regel.** Geboortedata omzetten naar geboortejaar.

**Implementatie.** In `proxy/generalization.py`:

```python
def generalize_birthdate(text: str, entities: list[Entity]) -> tuple[str, list[Entity]]:
    """Vervang gedetecteerde BIRTHDATE-entities door alleen het jaar.

    Voorbeeld: "Patiënt geboren op 15-03-1956" → "Patiënt geboren in 1956"

    De entity wordt aangepast: type wordt BIRTH_YEAR, generalized_to bevat
    "1956", original blijft "15-03-1956". Vault bewaart oorspronkelijke datum.
    """
```

**Belangrijke nuance.** Generalisering is **lossy**: na deze stap is de
oorspronkelijke datum weg uit de prompt. De vault houdt
`original=15-03-1956`, `generalized_to=1956`. Bij two_way de-pseudonimisering
geef je het origineel terug.

**Acceptatiecriterium.** Input `"geboren 03-04-1972"` → pseudonimized prompt
bevat `1972`, niet `03-04-1972`.

### BR-B02 — Postcodes → eerste 2 cijfers (PC2)

**Regel.** PC6 naar PC2 reduceren.

**Implementatie.** In `proxy/generalization.py`:

```python
def generalize_postcode(text: str, entities: list[Entity]) -> tuple[str, list[Entity]]:
    """1234 AB → 12. Entity-type verandert van POSTCODE_PC6 naar POSTCODE_PC2."""
```

**Acceptatiecriterium.** `"7411AB Deventer"` → `"74 Deventer"` (of `74` gevolgd
door eventueel pseudoniem voor de plaatsnaam).

### BR-B03 — Leeftijden ≥ 90 → "90+"

**Regel.** Leeftijden vanaf 90 samenvoegen tot categorie "90+".

**Implementatie.** AGE-entity wordt gedetecteerd via regex
`\b\d{1,3}\s?(?:jaar|jaarige?)\b`. Daarna:

```python
def generalize_age(age_str: str) -> str:
    age = int(re.search(r"\d+", age_str).group())
    return "90+" if age >= 90 else str(age)
```

**Acceptatiecriterium.** "92 jaar" → "90+ jaar" in pseudonimized prompt.
"45 jaar" → blijft "45 jaar".

### BR-B04 — Opname/ontslag/onderzoeksdata → maand+jaar

**Regel.** Behandeldata naar `YYYY-MM`.

**Implementatie.** Nieuwe entity-types: `ADMISSION_DATE`, `DISCHARGE_DATE`,
`EXAM_DATE`. Detectie via regex met contextwoorden:

```python
ADMISSION_DATE_PATTERN = re.compile(
    r"(?:opname|opgenomen|opnamedatum|opnemingsdatum)\D{0,20}"
    r"(\d{1,2}[-/]\d{1,2}[-/]\d{4})",
    re.IGNORECASE
)
```

Generalisering: `"opgenomen op 15-03-2024"` → `"opgenomen in 2024-03"`.

**Acceptatiecriterium.** Datum met contextwoord wordt gegeneraliseerd; losse
datum zonder contextwoord wordt mogelijk door BR-B01 als BIRTHDATE gepakt.

### BR-B05 — Zeldzame diagnoses → ICD-10 categorie of review

**Regel.** Diagnoses met prevalentie < 1:10.000 generaliseren of markeren.

**Implementatie v0.3.** Geen volledige prevalentie-database; pragmatische
aanpak:
- Detecteer ICD-10 codes via regex (zie BR-A02)
- Vergelijk met `data/icd10_rare.py` (set van ~30 expliciet gemarkeerde codes
  voor v0.3; v1.0 zou officiële RIVM/CBS-data gebruiken)
- Bij match → flag voor manual review (niet auto-generaliseren in v0.3)

```python
# data/icd10_rare.py
RARE_ICD10_CODES: set[str] = {
    "C92.4",  "E70.0",  "G71.0",  # ... ~30 codes
}
```

**Acceptatiecriterium.** "Patiënt heeft G71.0" → review queue met flag
"rare ICD-10 code". Reviewer kiest: generaliseer naar familiecode, of
behandel als gewone diagnose.

### BR-C01 — HMAC-SHA-256 pseudonimisering

**Regel.** Pseudoniem via HMAC-SHA-256 of sterker, met geheime sleutel.
Unsalted hash is verboden.

**Implementatie.** In `shared/crypto.py`:

```python
import hmac
import hashlib
import secrets
from pathlib import Path

def load_or_create_secret(secret_path: Path) -> bytes:
    """Laad de globale HMAC-sleutel; genereer 32 random bytes bij eerste run.

    Sleutel staat in secrets/global_secret.bin met file mode 0o600.
    """
    if secret_path.exists():
        return secret_path.read_bytes()
    key = secrets.token_bytes(32)
    secret_path.parent.mkdir(exist_ok=True)
    secret_path.write_bytes(key)
    secret_path.chmod(0o600)
    return key


def derive_session_key(global_secret: bytes, session_id: str) -> bytes:
    """Session-scoped key zodat cross-sessie linkbaarheid voorkomen wordt.

    Zelfde pseudoniem binnen één prompt, ander pseudoniem in andere sessie.
    """
    return hmac.new(global_secret, session_id.encode("utf-8"), hashlib.sha256).digest()


def make_pseudonym(session_key: bytes, original: str, entity_type: str) -> str:
    """Genereer een pseudoniem voor `original` binnen een sessie.

    Format: [TYPE-xxxxxx] waarbij xxxxxx = eerste 6 hex chars van
    HMAC-SHA-256(session_key, entity_type + ":" + original).
    """
    payload = f"{entity_type}:{original.strip()}".encode("utf-8")
    digest = hmac.new(session_key, payload, hashlib.sha256).hexdigest()
    short_type = SHORT_TYPE_CODES[entity_type]
    return f"[{short_type}-{digest[:6]}]"
```

**Type-code afkortingen** (3-letterig):

| EntityType | Code | EntityType | Code |
|---|---|---|---|
| BSN | BSN | MRN | MRN |
| NAME / PERSON | PER | EPD_ID | EPD |
| EMAIL | EML | KENTEKEN | KEN |
| PHONE | TEL | ORG | ORG |
| IBAN | IBN | LOCATION | LOC |
| POSTCODE_PC6 | PC6 | ICD10_CODE | ICD |
| POSTCODE_PC2 | PC2 | ADMISSION_DATE | ADM |
| BIRTHDATE | BDT | DISCHARGE_DATE | DCH |
| BIRTH_YEAR | BYR | PRODUCT | PRD |
| AGE | AGE | PROJECT | PRJ |
| ADDRESS | ADR | DIAGNOSIS | DGN |
| EXAM_DATE | EXM | | |

**Acceptatiecriterium.**
1. Pseudoniem-format `[XXX-aaaaaa]` (drie letters, streepje, 6 hex chars)
2. Zelfde input + zelfde session_id → zelfde pseudoniem
3. Verschillende session_id → verschillend pseudoniem voor zelfde input
4. Geen plain SHA-256, MD5, SHA-1 in het pseudoniem-pad; alleen HMAC-SHA-256

### BR-C06 — Eenweg default, tweeweg met documentatie; per-template per-EntityType override

**Regel.** Eenweg-pseudonimisering is default; tweeweg vereist gedocumenteerde
functionele noodzaak.

**Implementatie v0.3 — drie-laagse modus-bepaling:**

```
super-default (Config-pagina)   ← default: ONE_WAY
       │
       └── overrulebaar door template-default (Template-pagina, per template)
                  │
                  └── overrulebaar per EntityType (per template, per type)
```

**Modus-betekenis:**

| Modus | Vault bewaart? | Response wordt de-pseudonimiseerd? | Gedrag bij response |
|---|---|---|---|
| ONE_WAY | Ja (voor audit) | Nee | Pseudoniem blijft staan; gebruiker zoekt zelf op in vault als nodig |
| TWO_WAY | Ja | Ja | Pseudoniem in response → automatisch vervangen door origineel |

**Belangrijke ontwerpkeuze.** De vault bewaart in beide modi de mapping, omdat:
- Audit-trail consistent moet zijn (BR-G01)
- One_way moet **gedrag** veranderen, niet **opslag** (dat zou debugging breken)
- Strikte BR-C06-interpretatie (geen vault bij one_way) maakt de tool
  onbruikbaar voor de kern-use-case en wordt door BR-G01 alsnog vereist

**Documenteer in README** dat one_way in Pylades v0.3 = "pseudoniem blijft in
response", niet "geen mapping bewaard". Voor strikt-cryptografische one_way
zie v1.0.

**Schema-uitbreiding voor templates:**

```sql
ALTER TABLE templates ADD COLUMN default_mode TEXT;
-- NULL = gebruik super-default; anders 'one_way' of 'two_way'
ALTER TABLE templates ADD COLUMN mode_overrides TEXT NOT NULL DEFAULT '{}';
-- JSON: {"BSN": "one_way", "ORG": "two_way", ...}
ALTER TABLE templates ADD COLUMN two_way_justification TEXT;
-- vrije tekst; verplicht ingevuld als ergens two_way actief is
```

**Resolver-functie:**

```python
def effective_mode(
    template: Template,
    entity_type: EntityType,
    super_default: PseudonymizationMode,
) -> PseudonymizationMode:
    """Bepaal de effectieve modus voor één entity in een template-context."""
    override = template.mode_overrides.get(entity_type.value)
    if override:
        return PseudonymizationMode(override)
    if template.default_mode:
        return PseudonymizationMode(template.default_mode)
    return super_default
```

**UI-gedrag.**

Op Template-pagina:
- Dropdown "Template default": `Gebruik super-default | One-way | Two-way`
- Tabel met EntityType-rijen, kolom "Modus": `Gebruik template default | One-way | Two-way`
- Tekstveld "Onderbouwing two-way" verschijnt zodra een Two-way override actief is;
  validatie blokkeert opslaan als veld leeg is

Op Config-pagina:
- Dropdown "Super-default modus": `One-way (aanbevolen) | Two-way`
- Bij Two-way: rode banner "BR-C06 vereist documentatie waarom afwijken van
  one-way functioneel noodzakelijk is" + verplicht tekstveld

In Testruns side-by-side: per pseudoniem een klein label `[1w]` of `[2w]`
zodat de gebruiker ziet welke modus actief is per entity.

**Acceptatiecriterium.**
1. Super-default uit `config` tabel is `one_way`
2. Template zonder overrides erft super-default voor alle entities
3. Template-default overruled super-default; entity-override overruled
   template-default
4. Two_way override zonder onderbouwing kan niet opgeslagen worden
5. Response van Anthropic: pseudoniemen voor one_way entities blijven staan
   in de teruggegeven tekst; pseudoniemen voor two_way entities worden vervangen
   door originelen
6. Vault bevat mappings voor **beide** modi (audit-vereiste)

### BR-G01 — Volledig request en response loggen in eigen omgeving

**Regel.** Volledig request en response gelogd in eigen omgeving, geen
afhankelijkheid van provider-logging.

**Implementatie.** In `pylades-content.db`:

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    template_id INTEGER,
    original_prompt TEXT NOT NULL,           -- volledig origineel (v0.3: plaintext)
    pseudonymized_prompt TEXT NOT NULL,      -- wat naar Anthropic ging
    response_pseudonymized TEXT,             -- wat Anthropic teruggaf
    response_depseudonymized TEXT,           -- wat de client kreeg (only TWO_WAY entities vervangen)
    llm_provider TEXT,
    llm_model TEXT,
    avg_confidence REAL,
    review_required INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (template_id) REFERENCES templates(id)
);
```

**Acceptatiecriterium.** Elke proxy-call schrijft een complete audit-entry
inclusief beide versies van prompt en response, met session_id-koppeling
naar mappings in vault.

### BR-G02 — Pseudonimiseringsmapping separaat van content-log

**Regel.** Mapping separaat opgeslagen met afzonderlijke toegangscontrole.

**Implementatie.** Twee SQLite-databases met aparte file paths en POSIX-permissies.

```python
# shared/config.py — gebruik pydantic-settings
class Settings(BaseSettings):
    """Settings worden geladen uit .env of environment variables.

    pydantic-settings biedt type-safe configuratie zonder boilerplate:
    één klasse, automatische .env-loading, validatie op type.
    """
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: str = ""
    content_db_path: Path = Path("./pylades-content.db")
    vault_db_path: Path = Path("./pylades-vault.db")
    global_secret_path: Path = Path("./secrets/global_secret.bin")
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"
    spacy_model: str = "nl_core_news_md"
    proxy_port: int = 8080
    ui_port: int = 8501
```

```python
# shared/db.py
def init_databases() -> None:
    """Maak beide databases en zet POSIX-permissies."""
    _init_content_db()
    _init_vault_db()
    if settings.vault_db_path.exists():
        settings.vault_db_path.chmod(0o600)
```

Schema in `pylades-vault.db`:

```sql
CREATE TABLE mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    pseudonym TEXT NOT NULL,
    original TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_category TEXT NOT NULL,
    pseudonymization_mode TEXT NOT NULL,    -- one_way | two_way (per entity)
    confidence REAL NOT NULL,
    detection_layer TEXT NOT NULL,
    generalized_to TEXT,                    -- gegeneraliseerde vorm (BR-B), indien van toepassing
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, pseudonym),
    UNIQUE(session_id, original, entity_type)
);

CREATE INDEX idx_mappings_session ON mappings(session_id);
```

Aparte connection helpers:

```python
def get_content_connection() -> sqlite3.Connection: ...
def get_vault_connection() -> sqlite3.Connection: ...
```

**Niet gemixt importeren.** `proxy/audit.py` mag alleen
`get_content_connection` gebruiken; `proxy/mapping.py` alleen
`get_vault_connection`. In v0.3 bewaak je dit met **code review en een
dedicated test** (`tests/test_db_separation.py`). In v1.0 zou je een
strikte typing-check toevoegen (bv via mypy custom types).

**Acceptatiecriterium.**
1. Twee separate .db files bestaan na `init_databases()`
2. Vault-file heeft mode `0o600` (alleen owner read+write)
3. Content-db bevat geen mapping-tabel; vault-db bevat geen audit_log
4. `tests/test_db_separation.py` verifieert geen kruislingse imports

---

## Per module specs

### `shared/config.py`
Pydantic-settings klasse `Settings` met `.env`-loading. Velden zoals
hierboven onder BR-G02. Singleton `settings = Settings()`.

### `shared/crypto.py`
Helpers:
- `load_or_create_secret(path) -> bytes`
- `derive_session_key(secret, session_id) -> bytes`
- `make_pseudonym(session_key, original, entity_type) -> str`
- `validate_bsn_elfproef(bsn) -> bool`
- `validate_iban_checksum(iban) -> bool` (Mod-97 op landcode+rearranged)

### `shared/models.py`
Enums:
- `EntityType` — BSN, NAME, EMAIL, PHONE, IBAN, POSTCODE_PC6, POSTCODE_PC2,
  ADDRESS, BIRTHDATE, BIRTH_YEAR, AGE, MRN, EPD_ID, KENTEKEN, ICD10_CODE,
  DIAGNOSIS, ADMISSION_DATE, DISCHARGE_DATE, EXAM_DATE, ORG, LOCATION,
  PRODUCT, PROJECT
- `EntityCategory` — DIRECT_IDENTIFIER, QUASI_IDENTIFIER, FREE_TEXT,
  CLINICAL_SENSITIVE
- `DetectionLayer` — REGEX, SPACY, DEDUCE, LLM (runtime laag 2 = DEDUCE;
  SPACY blijft als eval/benchmark-waarde)
- `ReviewStatus` — PENDING, ACCEPTED, REJECTED, MODIFIED
- `PseudonymizationMode` — ONE_WAY, TWO_WAY

Pydantic v2 modellen:
- `Template` met velden: `id`, `groep`, `naam`, `beschrijving`,
  `llm_provider`, `llm_naam`, `prompt_tekst`, `max_tokens: int`,
  `use_llm: bool`, `default_mode: PseudonymizationMode | None`,
  `mode_overrides: dict[EntityType, PseudonymizationMode]`,
  `two_way_justification: str | None`, `sort_order: int` (zie PLAN §15a voor
  `max_tokens`/`use_llm` en de verplichte `{input}`-placeholder in
  `prompt_tekst`)
- `Entity` met velden: `original`, `entity_type`, `category`, `confidence`,
  `detection_layer`, `pseudonym`, `start`, `end`, `generalized_to: str | None`,
  `effective_mode: PseudonymizationMode`
- `ReviewItem` (matched tegen review_queue tabel)
- `DetectionResult` met `confident_entities` en `pending_review`
- `PseudonymizationResult` met `session_id`, `original`, `pseudonymized`,
  `entities`, `avg_confidence`, `review_required: bool`

### `shared/db.py`
Twee context managers `get_content_connection()` en `get_vault_connection()`.
`init_databases()` met permissies-fix. `get_config_value(key, default)` en
`set_config_value(key, value)` (beide op content-db).

### `proxy/detection.py`
Functies:
- `detect_regex(text) -> list[Entity]`
- `detect_deduce_with_status(text) -> tuple[list[Entity], LayerStatus]`
- `detect_llm_with_status(text, ...) -> tuple[list[Entity], LayerStatus]` — **faal soft**:
  bij Ollama-error, JSON-parse-error, of timeout: log warning, return lege lijst.
  Andere lagen hebben hun werk al gedaan; laag 3 is best-effort.

Orkestrator:
- `detect_all(text, use_llm=False) -> DetectionResult`

### `proxy/generalization.py`
Per BR een functie:
- `generalize_birthdate(text, entities) -> tuple[str, list[Entity]]`
- `generalize_postcode(text, entities) -> tuple[str, list[Entity]]`
- `generalize_age(text, entities) -> tuple[str, list[Entity]]`
- `generalize_treatment_dates(text, entities) -> tuple[str, list[Entity]]`
- `flag_rare_diagnoses(text, entities) -> tuple[str, list[Entity]]`

Orkestrator:
- `generalize_all(text, entities, config) -> tuple[str, list[Entity]]`

Elke functie respecteert config-flags.

### `proxy/pseudonymization.py`
- `pseudonymize(text, entities, session_id, template) -> str` — vervangt
  entities door HMAC-pseudoniemen; schrijft alle entities naar vault met
  hun effectieve modus
- `depseudonymize(text, session_id) -> str` — leest vault, vervangt alleen
  pseudoniemen waarvan `pseudonymization_mode == TWO_WAY` terug naar
  origineel; one_way pseudoniemen blijven staan

### `proxy/mapping.py` — `PseudonymManager`

Zelfstandige klasse die per sessie pseudoniem-state bijhoudt.

```python
class PseudonymManager:
    """Beheer pseudoniemen binnen één sessie.

    - Pseudoniem-generatie via HMAC-SHA-256 (BR-C01)
    - Schrijft mappings naar vault.db (BR-G02)
    - Onthoudt effectieve modus per entity zodat depseudonymize() weet
      welke wel/niet terug te vertalen
    """

    def __init__(self, session_id: str | None = None) -> None: ...

    def add_entity(
        self,
        original: str,
        entity_type: EntityType,
        confidence: float,
        detection_layer: DetectionLayer,
        effective_mode: PseudonymizationMode,
    ) -> str:
        """Genereer en bewaar pseudoniem; returnt het pseudoniem."""

    def persist(self) -> None:
        """Schrijf alle entities naar pylades-vault.db."""

    @classmethod
    def from_session(cls, session_id: str) -> "PseudonymManager":
        """Herlaad bestaande sessie uit vault."""

    def deanonymize(self, text: str) -> str:
        """Vervang alleen TWO_WAY pseudoniemen terug; sorteer op lengte
        (langste eerst) om partial matches te voorkomen."""
```

### `proxy/review.py`
- `enqueue(items, session_id)` — schrijft naar review_queue
- `get_pending(session_id)`
- `decide(item_id, status, modified_type=None)`
- `all_resolved(session_id) -> bool`

### `proxy/audit.py`
- `log_request(...)` — schrijft naar content-db
- `get_recent_logs(limit)`, `get_log_by_id(id)`

### `proxy/main.py`
> **Verouderd t.o.v. implementatie.** Het definitieve contract gebruikt een
> eigen body-shape `{template_id, dossier, resume_session}` met verplichte
> `template_id` en één `{input}`-placeholder; geen `X-Pylades-*`-headers meer.
> Zie PLAN §15a voor de gebouwde flow.

`POST /v1/messages` flow (oorspronkelijke spec):
1. Lees body, extract user-text + template_id uit custom header
   `X-Pylades-Template-Id` (optioneel; zonder template wordt super-default
   gebruikt voor alle entities)
2. `detect_all()` → `DetectionResult`
3. Als `pending_review` niet leeg → schrijf naar review_queue, return HTTP
   423 met `{"session_id", "review_url"}`
4. Anders: `generalize_all()` → resolve `effective_mode` per entity →
   `pseudonymize()` → POST naar Anthropic
5. `depseudonymize(response)` (vervangt alleen two_way pseudoniemen)
6. Log naar content-db
7. Return response

Anthropic-only in v0.3: bij `llm_provider != "anthropic"` in template
return HTTP 501 `Not Implemented` met verwijzing naar v1.0.

### `ui/Home.py`
Streamlit hoofdpagina met status-checks (proxy, ollama, DEDUCE, beide DBs)
en welkomstuitleg. Bij elk failing onderdeel: install-command tonen in
`st.code(...)`.

### `ui/pages/1_Templates.py`
- Overzichts-tab + Nieuw/Bewerken-tab
- In Bewerken-tab een sectie "Pseudonimiseringsmodus" met:
  - Dropdown template-default (`Gebruik super-default | One-way | Two-way`)
  - Tabel met EntityType-rijen, drie kolommen:
    1. **EntityType** (bv `BSN`, `PERSON`, `ICD10_CODE`)
    2. **Modus-override** (dropdown: `Gebruik template default | One-way | Two-way`)
    3. **Resulterende modus** (read-only label dat live wordt berekend via
       `effective_mode(template, entity_type, super_default)`, met bron-
       annotatie: `one_way (super-default)`, `two_way (template-default)`,
       of `one_way (override)`)
  - Tekstveld two_way-onderbouwing (verplicht bij two_way override of
    two_way template-default)

De derde kolom is essentieel: zonder die kolom moet de gebruiker mentaal
de drie-laagse resolver uitvoeren om te begrijpen wat er gebeurt. Door
het resultaat expliciet te tonen voorkom je interpretatiefouten en versterk
je het transparantie-principe dat onder BR-C06 schuilt.

### `ui/pages/2_Testruns.py`
- Template-keuze, placeholder-invul
- Knop "Analyseer anonimisatie" → side-by-side
- Per entity in mapping-tabel: kolom "Modus" met `[1w]` of `[2w]` label
- Verstuur-knop: ook one_way entities krijgen pseudoniem; response toont
  one_way pseudoniemen letterlijk terug

### `ui/pages/3_Review_Queue.py`
- Sessie-selectie + pending items
- Per item: context-snippet, accept/reject/change-buttons
- "Hervat sessie" knop

### `ui/pages/4_Audit.py`
- Overzicht recente requests
- Detail-view met 4 tabs: Origineel, Pseudonimized, Response (pseud),
  Response (terug)

### `ui/pages/5_Config.py`
- Threshold-sliders per detectielaag
- Generalisering aan/uit per BR-B-regel
- Super-default modus dropdown met waarschuwing bij two_way
- **Sleutelrotatie-blok** met meerstaps-flow:
  1. Knop "Roteer globale HMAC-sleutel"
  2. Eerste bevestigingsdialoog: waarschuwing dat alle bestaande mappings
     onbruikbaar worden (oude pseudoniemen in audit-log zijn niet meer
     herleidbaar na rotatie)
  3. Checkbox "Exporteer huidige mappings naar CSV vóór rotatie"
     (standaard aan); bij actief: download-button die een CSV genereert met
     kolommen `session_id, pseudonym, original, entity_type, entity_category,
     pseudonymization_mode, created_at` uit vault.db
  4. Tweede bevestiging via typed-confirmation: tekstveld waarin gebruiker
     letterlijk `ROTEER` moet typen voordat de actie wordt geactiveerd
  5. Pas dan: oude sleutel naar `secrets/global_secret.bin.archived-<timestamp>`
     (mode 0o600), nieuwe sleutel gegenereerd via `secrets.token_bytes(32)`

**README-vermelding voor sleutelrotatie:** geëxporteerde CSV bevat alle
originals plaintext; gebruiker is zelf verantwoordelijk voor veilige opslag
en eventuele vernietiging na archivering. In v1.0 wordt CSV-export
versleuteld met een wachtwoord-gebaseerde KDF.

### `data/fixtures.py`
Fictieve Nederlandse cases. Inclusief:
- Geldige BSN `123456782` (passeert elfproef)
- Ongeldige BSN `123456789` (faalt elfproef; voor negative test)
- Fictief MRN `MRN1234567`, EPD-id `EPD-789012`
- ICD-10 codes (zowel veelvoorkomend `J45.0` als zeldzaam `G71.0`)
- Opnamedatum met contextwoord
- Postcode `7411AB`
- Leeftijden onder en boven 90

Minstens 5 testcases die de drielagige pipeline plus generalisering plus
review-queue triggeren.

---

## Niet-functionele eisen

- Type hints overal; geen `Any` tenzij echt nodig
- PEP 8 / Ruff-clean; line length 100
- Mypy strict voor `shared/` en `proxy/`
- Async waar het hoort
- Geen `print()`; gebruik `logging` per module
- Errors expliciet: vang specifieke excepties, geen bare `except`
- Per module-oplevering: sectie "Wat hier gebeurt" (max 200 woorden) met
  (1) kerngedachte, (2) belangrijkste design-keuze + waarom, (3) één ding
  dat iemand makkelijk verkeerd zou doen

---

## Bouwvolgorde

1. `pyproject.toml`, `.env.example`, `.gitignore`, `README.md` (met disclaimer
   + bekende v0.3-beperking)
2. `shared/config.py`, `shared/models.py`, `shared/crypto.py`
3. `shared/db.py` + `tests/test_db_separation.py` (eerst testen!)
4. `data/fixtures.py` + `data/icd10_rare.py`
5. `proxy/detection.py` + `tests/test_detection.py`
6. `proxy/generalization.py` + `tests/test_generalization.py`
7. `proxy/pseudonymization.py` + `proxy/mapping.py` +
   `tests/test_pseudonymization.py` + `tests/test_mapping.py`
8. `proxy/review.py` + `tests/test_review.py`
9. `proxy/audit.py`
10. `proxy/main.py` + `tests/test_proxy.py`
11. `ui/Home.py`
12. `ui/pages/1_Templates.py`
13. `ui/pages/5_Config.py`
14. `ui/pages/3_Review_Queue.py`
15. `ui/pages/2_Testruns.py`
16. `ui/pages/4_Audit.py`

Na elke stap: `uv run pytest tests/ -v` en `uv run ruff check .`

---

## Acceptatiecriteria

1. `uv run uvicorn proxy.main:app` start zonder errors
2. `uv run streamlit run ui/Home.py` toont vier groene status-cards
3. Twee aparte .db files bestaan; vault heeft mode `0o600`
4. Templates aanmaken/bewerken/verwijderen werkt; per-EntityType modus-
   override kan worden ingesteld
5. Two_way override zonder onderbouwing kan niet opgeslagen worden
6. Testrun met fixture-prompt geeft side-by-side view; pseudoniemen in
   formaat `[XXX-aaaaaa]`; per pseudoniem zichtbaar `[1w]` of `[2w]` label
7. Pseudoniem voor zelfde original is identiek binnen sessie, verschillend
   tussen sessies
8. Valide BSN (`123456782`) wordt gedetecteerd; invalide (`123456789`) wordt
   afgewezen door elfproef
9. Geboortedatum `15-03-1972` → `1972` in pseudonimized prompt
10. Postcode `7411AB` → `74` in pseudonimized prompt
11. Leeftijd "92 jaar" → "90+" in pseudonimized prompt
12. Opnamedatum met contextwoord `"opgenomen op 15-03-2024"` → `"2024-03"`
13. Detectie onder threshold blokkeert proxy-call met HTTP 423; review queue
    toont items; na alle accept → hervatte call werkt
14. Audit-log in content.db bevat full original + pseudonymized; mappings
    in vault.db apart
15. Live proxy-call round-trip: two_way pseudoniemen in response worden
    correct terugvertaald; one_way pseudoniemen blijven staan
16. Vault bevat mappings voor zowel one_way als two_way entities
17. Sleutelrotatie-flow vereist typed-confirmation `ROTEER`; standaard
    CSV-export-checkbox staat aan; na rotatie wordt oude sleutel gearchiveerd
    en een nieuwe sessie produceert nieuwe pseudoniemen
18. `tests/test_db_separation.py` slaagt: audit-module raakt vault niet aan
19. Alle pytest-tests slagen
20. Ruff geeft geen warnings

---

## Wat NIET te doen

- Geen Docker, geen docker-compose
- Geen authenticatie / login op de UI (v1.0)
- Geen encryptie van data-at-rest (v1.0)
- Geen async-iterator streaming (v1.0)
- Geen React/Vue frontend — Streamlit is de UI
- Geen ORM — `sqlite3` direct
- Geen LangChain, LlamaIndex
- Geen overbodige abstracties
- Geen `# TODO` zonder concreet vervolg
- Geen unsalted hash, MD5, SHA-1 in pseudoniem-pad
- Geen kruisreferenties tussen content.db en vault.db buiten via session_id
- Geen LLM-provider-abstractielaag (Anthropic-only; v1.0 maakt agnostiek)

---

## Bij oplevering

1. `tree` van het project
2. Output van `uv run pytest tests/ -v`
3. Demo-script: stappen om de POC live te zien werken, met minimaal:
   - Eén testrun die direct doorgaat (geen review)
   - Eén testrun die door review queue heen moet
   - Eén template met mixed one_way / two_way overrides die laat zien dat
     response sommige entities terugvertaalt en andere niet
4. Sectie "v1.0 roadmap" met expliciete verwijzing naar de BR's die in v0.3
   buiten scope vielen

Begin met je eventuele verhelderingsvragen, of als alles helder is: bouw stap 1.
