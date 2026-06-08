# Pylades v0.2.3

> Lokale pseudonimiseringsproxy voor extern LLM-gebruik in de zorg.
> **Huidige release:** v0.2.3 · **doelversie:** v0.3 · zie [PLAN.md](PLAN.md).
> **Pylades** is in de Griekse mythologie de trouwe metgezel van Orestes —
> een archetype van onvoorwaardelijke vriendschap: een metgezel aan wie je
> alles toevertrouwt.

Pylades is een lokale privacy-laag voor zorgorganisaties die AI willen inzetten
zonder persoonsgegevens naar externe LLM-aanbieders te sturen. De proxy
detecteert en vervangt gevoelige informatie vóór verzending, en zet — waar
geconfigureerd — pseudoniemen terug in het antwoord.

Pylades draait twee processen naast elkaar op `localhost`:

- **FastAPI proxy** op `:8080` — luistert op het pad `POST /v1/messages` met
  een **eigen body-contract** (`template_id` + `dossier`, geen
  Anthropic-pass-through), pseudonimiseert opdrachten vóór verzending naar
  Anthropic en de-pseudonimiseert de response voor `TWO_WAY` entities. De
  teruggegeven response blijft Anthropic-vormig.
- **Streamlit UI** op `:8501` — Home (testrun-flow voor zowel technische
  als niet-technische gebruikers met Compact/Uitgebreid-modus), Status,
  Opdrachten, Review-queue, Audit en Config.

Twee gescheiden SQLite-databases vormen de kern van de defense-in-depth:

- `pylades-content.db` — templates, audit_log, sessions, review_queue, config
- `pylades-vault.db` — pseudoniem ↔ origineel mappings (file-mode `0o600`)

---

## Status

Pylades v0.2.3 is een **proof of concept** op één persoonlijke machine.
Productie-inzet op echte zorgdata is **niet** toegestaan zonder formele
FG/DPO-toetsing. Zie [Productie-disclaimer](#productie-disclaimer-verplicht-lezen)
hieronder.

---

## Wat Pylades doet

Pylades zit **tussen** jouw applicatie en een extern LLM. Voordat tekst het netwerk op gaat:

1. **Detecteert** het persoonsgegevens — regex, spaCy NER, en optioneel een
   lokaal Ollama-model voor jargon en productnamen.
2. **Generaliseert** waar mogelijk — geboortedatum wordt jaar, postcode PC6
   wordt PC2, leeftijd ≥90 wordt `"90+"`.
3. **Pseudonimiseert** wat overblijft — deterministische HMAC-pseudoniemen,
   opgeslagen in een aparte vault-database.
4. **Stuurt** alleen de geanonimiseerde opdracht naar Anthropic.
5. **De-pseudonimiseert** — alleen voor entities die als `TWO_WAY` zijn
   geconfigureerd — het antwoord voordat het teruggaat naar de caller.

Alles wat binnenkomt en uitgaat wordt gelogd in een audit trail.

---

## Hoe het werkt

```
Jouw app  →  Pylades proxy (:8080)  →  Anthropic API
                  ↓
            Streamlit UI (:8501)
            Home (testrun) · Status · Opdrachten · Review-queue · Audit · Config
```

1. Je kiest een **template** (opdracht + entity-configuratie) in de UI of via
   `template_id` in de API-call.
2. Pylades verwerkt je **dossier**-tekst door de detectie-, generalisatie- en
   pseudonimisatiepipeline.
3. Bij onzekere detecties pauzeert de proxy (`HTTP 423 Locked`) en stuurt je
   naar de **Review Queue** — jij beslist wat er gebeurt.
4. Na goedkeuring gaat de call door; het antwoord komt terug (de-pseudonimiseerd
   waar `TWO_WAY` actief is).

Zie [Architectuur op één pagina](#architectuur-op-één-pagina) voor de volledige
pipeline.

---

## Wie bepaalt wat er gebeurt

Pylades is **configurabel**, niet een black box:

| Laag | Wie | Wat |
| --- | --- | --- |
| Super-default | Config-pagina | Standaardmodus (`ONE_WAY` / `TWO_WAY`) voor alle entities |
| Template-default | Per template | Override van super-default |
| Per-entity override | Per template | Fijne controle per entity-type |
| Review Queue | Mens | Beslissing bij lage confidence |
| Sleutelrotatie | Beheerder | HMAC-sleutel roteren met export + typed confirmation |

`TWO_WAY` vereist een gedocumenteerde onderbouwing (BR-C06). De UI blokkeert
opslag als die ontbreekt.

---

## Productie-disclaimer (verplicht lezen)

Pylades v0.2.3 implementeert 12 specifieke business rules uit een
zorg-georiënteerde functionele specificatie (richting doelversie v0.3), maar is **niet productie-geschikt
voor zorgdata**. Ontbrekende productie-vereisten zijn onder andere:

- Medisch NER-model (BR-A03)
- K-anonimiteit en l-diversity (BR-D-serie)
- DPA met LLM-aanbieder (BR-E-serie)
- TLS 1.3 met mTLS (BR-F01)
- Rol-gebaseerde autorisatie voor de-pseudonimisering (BR-H01)
- DPIA en FG-goedkeuring (BR-I-serie)
- HSM-sleutelbeheer (BR-C02)
- Tamper-evident logging (BR-G04)

Deze worden onderdeel van Pylades v1.0. **Vóór elk productie-gebruik op
zorgdata is formele FG/DPO-toetsing verplicht.**

---

## Bekende v0.3-beperking

De originele opdracht wordt **plaintext** opgeslagen in
`audit_log.original_prompt` binnen `pylades-content.db`. Compromis van alleen
de content-DB lekt daardoor de oorspronkelijke gevoelige inhoud, zelfs zonder
toegang tot de vault.

De mapping/content-separation (BR-G02) beschermt v0.3 daarom vooral tegen
**runtime-exfiltratie** (netwerk, provider-logs, geïntercepteerde responses),
niet tegen diefstal van de audit-database zelf.

In v1.0 wordt `original_prompt` óf encrypted-at-rest opgeslagen, óf vervangen
door `original_prompt_hash` voor integriteitsbewijs zonder inhoud.

---

## Versie

- **Huidige release:** v0.2.3 — enige bron: [`shared/version.py`](shared/version.py)
  (sync met `pyproject.toml`; test: `tests/test_version.py`).
- **Doelversie:** v0.3 (`TARGET_VERSION` in [`shared/version.py`](shared/version.py)).
- **Spec v0.3:** [PLAN.md](PLAN.md) en [SPEC-v0.3.md](SPEC-v0.3.md).

---

## Tech stack

| Component | Technologie |
| --- | --- |
| Proxy | FastAPI, Python 3.11+ |
| UI | Streamlit |
| Detectie laag 1 | Regex (BSN, e-mail, telefoon, IBAN, …) |
| Detectie laag 2 | spaCy `nl_core_news_md` |
| Detectie laag 3 | Ollama + `qwen3:1.7b` (optioneel, standaard uit) |
| Pseudonimisering | HMAC-SHA-256, session-key |
| Content-DB | SQLite (`pylades-content.db`) |
| Vault-DB | SQLite (`pylades-vault.db`, mode `0o600`) |
| LLM-provider | Anthropic (Messages API) |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Tests | pytest (volledige suite groen) |

---

## Doelplatform

- Eén persoonlijke machine, één gebruiker — primair getest op **macOS**
  (M1 MacBook Pro **8 GB**); **Windows 10/11** wordt ondersteund via
  `scripts/pylades_services.py` (netstat/taskkill)
- Geen multi-tenancy, geen auth op de UI
- Eén LLM-provider: Anthropic
- Fictieve Nederlandse testdata

Multi-tenancy, encryptie at rest, provider-agnostiek, productie-deployment,
Docker/CI/CD en streaming responses zijn **v1.0** — buiten scope van deze
release.

---

## Aan de slag

Snelste pad naar een werkende demo. Vereist: **Git**, Python 3.11+ en
[`uv`](https://docs.astral.sh/uv/). GitHub CLI (`gh`) is **niet** nodig om Pylades
lokaal te draaien — alleen om zelf naar GitHub te pushen.

### macOS / Linux

```bash
git clone git@github.com:PatrickWillems73/Pylades.git pylades && cd pylades
cp .env.example .env          # vul ANTHROPIC_API_KEY in
uv sync --extra dev
uv run python -m spacy download nl_core_news_md
uv run python scripts/pylades_services.py restart
```

### Windows (PowerShell)

```powershell
git clone https://github.com/PatrickWillems73/Pylades.git pylades
cd pylades
Copy-Item .env.example .env   # vul ANTHROPIC_API_KEY in
uv sync --extra dev
uv run python -m spacy download nl_core_news_md
uv run python scripts/pylades_services.py restart
```

`pylades_services.py` detecteert Windows automatisch (`netstat` + `taskkill` i.p.v.
`lsof`). Vault-permissies (`0o600`) zijn op NTFS beperkter dan op macOS/Linux;
voor een lokale demo is dat geen blocker.

**Git op Windows:** `git` zit **niet** standaard op een schone Windows-installatie.
Installeer [Git for Windows](https://git-scm.com/download/win) (of via
`winget install Git.Git`) vóór `git clone`. Controleer met `git --version`.
Bij HTTPS-clone kan Git om inloggen vragen (browser of
[Personal Access Token](https://github.com/settings/tokens)) — dat is normaal
en vereist geen GitHub CLI.

Open daarna `http://localhost:8501` (UI) en `http://localhost:8080/docs` (API).
Voor een stap-voor-stap demo: [DEMO.md](DEMO.md).

---

## Installatie

Vereist: **Git**, Python 3.11+ en [`uv`](https://docs.astral.sh/uv/). Op macOS
staat Git vaak al via Xcode Command Line Tools; op Windows zie de noot bij
[Aan de slag](#aan-de-slag).

### 1. Repo klaarzetten

macOS / Linux (bash):

```bash
git clone git@github.com:PatrickWillems73/Pylades.git pylades
cd pylades
cp .env.example .env
# Vul ANTHROPIC_API_KEY in .env
```

Windows (PowerShell; HTTPS-clone werkt zonder SSH-keys):

Installeer eerst [Git for Windows](https://git-scm.com/download/win) als
`git --version` faalt.

```powershell
git clone https://github.com/PatrickWillems73/Pylades.git pylades
cd pylades
Copy-Item .env.example .env
# Vul ANTHROPIC_API_KEY in .env
```

### 2. Python-omgeving + dependencies

```bash
uv sync --extra dev
```

`uv sync` maakt een `.venv` en installeert alle pinned dependencies inclusief
test-tooling (pytest, ruff, mypy) en **streamlit-extras** (metric-card-styling op
de UI-homepage).

Optioneel voor **ui_preview.py** (shadcn-/option-menu-demo's):

```bash
uv sync --extra dev --extra preview
```

### 3. spaCy NL-model (laag 2)

```bash
uv run python -m spacy download nl_core_news_md
```

Dit downloadt ~50 MB en hoeft maar één keer.

### 4. Ollama (laag 3, optioneel)

Laag 3 staat **standaard uit**. Activeer alleen als je een lokaal LLM wilt
gebruiken voor jargon- en productnaam-detectie:

```bash
# Installeer Ollama: https://ollama.com/download
ollama serve            # in eigen terminal laten draaien
ollama pull qwen3:1.7b  # ~1.4 GB
```

### 5. Eerste run: secrets en databases

Bij de eerste proxy-start:

- `secrets/global_secret.bin` wordt automatisch aangemaakt (32 random bytes,
  mode `0o600`).
- `pylades-content.db` en `pylades-vault.db` worden geïnitialiseerd.
  De vault krijgt mode `0o600`.

---

## Gebruik

Open twee terminals:

```bash
# Terminal 1: proxy
uv run uvicorn proxy.main:app --port 8080

# Terminal 2: UI
uv run streamlit run ui/Home.py
```

`ui/app.py` is een dunne compat-shim (roept `Home.py` aan) voor oude `streamlit run ui/app.py`-commando’s; in het menu zie je dan nog **app** als label in plaats van **Home**.

Of beide poorten in één keer (her)starten — output gaat naar `logs/proxy.log` en
`logs/streamlit.log`. Werkt op **macOS, Linux en Windows**:

```bash
uv run python scripts/pylades_services.py restart   # default
uv run python scripts/pylades_services.py stop
uv run python scripts/pylades_services.py status
```

### Streamlit-werkbalk (standaard ingesteld)

In `.streamlit/config.toml` staat:

- **`client.showErrorLinks = false`** — geen knoppen *Ask Google* en *Ask
  ChatGPT* bij een Python-exception in de browser (handig voor
  privacy-gevoelige fouten; stacktrace blijft in de terminal).
- **`client.toolbarMode = "viewer"`** — verbergt de **Deploy**-knop en andere
  *developer*-opties in het menu rechtsboven, waaronder **Rerun** en **Clear
  caches**. Wil je die tijdens ontwikkeling terug, zet dan tijdelijk
  `toolbarMode = "developer"` of `auto`.

Korte uitleg van wat Streamlit daar mee bedoelt:

| Optie | Waarvoor |
| --- | --- |
| **Rerun** | Voert het hele script opnieuw uit vanaf de bovenkant. Handig als je code of externe data hebt gewijzigd en de app-state wilt verversen. |
| **Auto rerun** | Blijft het script opnieuw starten terwijl het bronbestand op schijf wijzigt (live reload tijdens je typt). |
| **Clear caches** | Wist `@st.cache_data` / `@st.cache_resource` en andere Streamlit-caches. Gebruik dit als je stale waarden ziet na een codewijziging; Pylades gebruikt weinig caching in de UI, dus meestal niet nodig. |

Stuur een request naar de proxy met het v0.3-bodycontract — `template_id`
en `dossier` zijn verplicht; `model`, `max_tokens` en `provider` leven
server-side op de gekozen opdracht-template en kunnen door de client niet
overruled worden:

```bash
curl -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "dossier": "Mevrouw Pietersen, BSN 123456782, woont op 1011AB."
  }'
```

De server stelt de opdracht zelf samen door `{input}` in
`Template.prompt_tekst` te vervangen door je dossier-tekst.

Wanneer detectie low-confidence items vindt, retourneert de proxy
`HTTP 423 Locked` met een `session_id` en `review_url` — open de
**Review Queue** in de UI, beslis per item, en herhaal de call met
**hetzelfde** `template_id` + `dossier` plus een extra body-veld
`resume_session: "<session_id>"`.

---

## Pseudonimiseringsmodi

Pylades kent twee modi per entity:

- **ONE_WAY** (super-default, aanbevolen): pseudoniem blijft in de response
  staan. Wil je het origineel weten, dan zoek je dat op in de vault.
- **TWO_WAY**: pseudoniem in de response wordt automatisch teruggezet naar
  het origineel.

De effectieve modus per entity wordt bepaald door drie lagen:

1. Super-default (Config-pagina)
2. Template-default (per template, kan super-default overrulen)
3. Per-entity override (per template, kan template-default overrulen)

`TWO_WAY` vereist een gedocumenteerde onderbouwing (BR-C06). De UI blokkeert
opslag als die ontbreekt.

> **Ontwerpkeuze.** De vault bewaart de mapping in **beide** modi. `ONE_WAY`
> verandert het *gedrag* (response-handling), niet de *opslag* — anders zou
> de audit-trail (BR-G01) breken. Strict-cryptografische one-way komt in v1.0.

---

## Sleutelrotatie

Op de Config-pagina staat een rotatie-flow met vijf stappen:

1. Knop "Roteer globale HMAC-sleutel".
2. Bevestigingsdialoog met waarschuwing dat oude pseudoniemen onleesbaar
   worden.
3. Checkbox "Exporteer huidige mappings naar CSV vóór rotatie" (standaard
   aan).
4. Typed-confirmation: tekstveld waarin je letterlijk `ROTEER` moet typen.
5. Oude sleutel wordt verplaatst naar
   `secrets/global_secret.bin.archived-<timestamp>` (mode `0o600`); nieuwe
   sleutel wordt gegenereerd.

> **Let op.** De CSV-export bevat alle originals in plaintext. Sla 'm veilig
> op en vernietig hem als hij niet meer nodig is. In v1.0 wordt deze export
> versleuteld met een wachtwoord-gebaseerde KDF.

---

## Status project (v0.3-spec)

Wat er nu staat (release v0.2.3):

- Volledige proxy-pipeline: detect → generalize → pseudonymize → Anthropic →
  de-pseudonymize
- Streamlit UI: Home (testrun-flow met Compact/Uitgebreid-modus), Status,
  Opdrachten, Review-queue, Audit, Config
- Volledige geautomatiseerde testsuite groen (`uv run pytest tests/ -v`)
- Demo-scenario in [DEMO.md](DEMO.md)
- Template-vlag `use_llm` om laag 3 (Ollama) per template aan/uit te zetten

Wat nog open staat vóór productie:

- Praktijktests met echte (geanonimiseerde) zorgdata
- FG/DPO-toetsing
- Alle v1.0-vereisten uit de roadmap hieronder

---

## Ontwikkelen

```bash
# Tests
uv run pytest tests/ -v

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run mypy
```

Conventies:

- Python 3.11+, PEP 8, line length 100.
- Type hints overal; geen `Any` tenzij echt nodig.
- Mypy strict voor `shared/` en `proxy/`.
- Docstrings in **Nederlands** voor publieke functies; inline NL-commentaar
  alleen waar logica niet voor zichzelf spreekt.
- Geen `print()`; gebruik `logging` per module.
- Geen bare `except`.

---

## Architectuur op één pagina

```
[Cursor / Python script / Claude Desktop]
            │ HTTPS (Pylades body-contract: template_id + dossier)
            ▼
[FastAPI proxy :8080]
   ├─► Stage 1: DETECT  — regex → spaCy NL → (Ollama, optioneel)
   │   Confidence < threshold? → REVIEW QUEUE → pauze
   ├─► Stage 2: GENERALIZE — geboortedatum→jaar, PC6→PC2, leeftijd ≥90 → "90+",
   │                          opnamedatum → maand-jaar, zeldzame ICD-10 → review
   ├─► Stage 3: PSEUDONYMIZE — HMAC-SHA-256 met session-key, vault-write voor
   │                            zowel ONE_WAY als TWO_WAY entities
   ├─► HTTPS POST → api.anthropic.com/v1/messages
   ├─► Response → de-pseudonimiseer alleen TWO_WAY entities
   └─► Audit-log volledig request+response in content.db

[Streamlit UI :8501]
   ├─► Home            (testrun-flow; Compact + Uitgebreid)
   ├─► Status          (proxy/Ollama/spaCy/DB-health)
   ├─► Opdrachten       (per-EntityType modus-override)
   ├─► Review-queue    (manual review)
   ├─► Audit           (recente requests)
   └─► Config          (thresholds, super-default, generalisering, rotatie)
```

---

## Roadmap naar v1.0

Buiten scope van v0.3, expliciet geadresseerd in v1.0:

- **Multi-tenancy en authenticatie** — UI + proxy, rol-gebaseerde autorisatie
  voor de-pseudonimisering (BR-H01)
- **Encryptie at rest** — `pylades-content.db` en/of `original_prompt_hash`
  i.p.v. plaintext audit-opdrachten
- **Provider-agnostiek** — OpenAI, Google, on-premise modellen via adapter-laag
- **Medisch NER** (BR-A03) — gespecialiseerd model naast regex/spaCy
- **K-anonimiteit + l-diversity** (BR-D-serie) — statistische privacy-garanties
- **DPA-template** met LLM-aanbieder (BR-E-serie)
- **TLS 1.3 + mTLS** (BR-F01)
- **DPIA en FG-goedkeuringsflow** (BR-I-serie)
- **HSM-sleutelbeheer** (BR-C02), tamper-evident audit (BR-G04)
- **Streaming responses**, Docker, CI/CD
- **Foto- en biometrische detectie** (uitgesloten in BR-A02)
- **Password-KDF** voor sleutelrotatie-CSV-export

---

## Bijdragen

Welkom. Lees [CONTRIBUTING.md](CONTRIBUTING.md) voor spelregels en workflow.
Bijdragers aan broncode tekenen de [Contributor License Agreement](CLA.md).
Optionele gebruikersondersteuning (gratis of betaald) kan via [SUPPORTERS.md](SUPPORTERS.md).

De software is bedoeld voor **niet-commercieel** gebruik (zie licentie).
Commercieel gebruik vereist aparte afspraken met de oprichters.

---

## Licentie

Copyright (c) 2026 Siebrand Zoethout en Patrick Willems.

Deze software wordt vrijgegeven onder de **PolyForm Noncommercial License
1.0.0**. Zie [LICENSE.md](LICENSE.md) voor de volledige tekst.

De broncode is openbaar en vrij te gebruiken voor niet-commerciële
doeleinden — *source-available*, dus geen "open source" in de zin van de
[Open Source Definition](https://opensource.org/osd) (die commercieel gebruik
niet mag beperken).

---

## Oprichters

- Siebrand Zoethout
- Patrick Willems
