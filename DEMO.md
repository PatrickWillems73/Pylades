# Pylades — Demo

> Doelversie v0.3 — demo-scenario's voor acceptatie; releaseversie staat in
> `shared/version.py`.

Drie reproduceerbare scenario's die samen de complete pijplijn raken:
clean round-trip, manual review, en gemengde modus-overrides. Geschreven
voor iemand die de proxy lokaal draait en voor het eerst wil bekijken
wat Pylades doet.

> **Voorwaarden:** `uv sync --extra dev` is uitgevoerd, `ANTHROPIC_API_KEY`
> staat in `.env`, en DEDUCE is beschikbaar (standaard via `uv sync`).
> Volg [README.md](README.md) § Installatie als dit nog niet klopt.
>
> **Opdrachten:**
> Maak minimaal één opdracht op **Opdrachten** (met `{input}` in de
> opdrachttekst) vóór je Scenario 1 start, of gebruik een bestaande rij.

## 0. Start

In twee terminals:

```bash
# Terminal 1 — proxy op :8080
uv run uvicorn proxy.main:app --port 8080

# Terminal 2 — UI op :8501
uv run streamlit run ui/Home.py
```

Op macOS: **`pylades.command`** (dubbelklik). Op Windows: **`pylades.cmd`**
(dubbelklik). Of platform-onafhankelijk:
`uv run python scripts/pylades_services.py restart` — zie
[README.md](README.md).

Open <http://localhost:8501> in een browser. Je landt op **Home**
(testrun). Controleer op **Status** (sidebar):

| Kaart | Vereist voor demo? |
| --- | --- |
| Proxy | **Ja** — groen |
| DEDUCE | **Ja** — groen |
| Databases | **Ja** — groen |
| Ollama | **Nee** — rood is OK (laag 3 staat default uit; `use_llm=False`) |

---

## Scenario 1 — Clean round-trip

**Doel.** Eén request met een BSN doorvoeren zonder review-items en zien
dat de response terugkomt met pseudoniemen op de plek waar de BSN stond
(super-default `ONE_WAY` — geen terugvertaling in de response).

### Via de UI (Compact)

1. Open **Home**. Bovenaan: modus **Compact** (default) of **Uitgebreid**
   (curl, raw upstream-body, mapping-tabel, JSON-response).
2. Kies een opdracht in de radio-lijst (`groep · naam`; in Uitgebreid ook
   `id=…`). De opdrachttekst moet `{input}` bevatten.
3. Plak in het dossier-veld bijvoorbeeld:
   `"Patiënt heeft BSN 123456782 en woont op 1011AB."`
   (zelfde BSN als in `data/fixtures.py` → `VALID_BSN`.)
4. Klik **Start** — dry-run: detectie + preview, **geen** upstream-call,
   **geen** audit-write.
5. Controleer entiteit-kaartjes (BSN, postcode). Klik **Verstuur naar
   extern LLM**.

### Via curl

Gebruik het `template_id` van jouw opdracht (niet per se `1`):

```bash
curl -sS -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "dossier": "Patiënt heeft BSN 123456782 en woont op 1011AB."
  }' | jq .
```

Vervang `template_id` als jouw opdracht een ander id heeft.

### Wat je ziet

- HTTP **200**, response-header `X-Pylades-Session: <uuid-hex>`.
- Response-body: assistant-tekst van Claude. Met super-default `ONE_WAY`
  blijven pseudoniemen als `[BSN-xxxxxx]` en `[PC6-xxxxxx]` in de
  response staan (6 hex-tekens na het streepje).

### Bevestig in de UI

1. Open **Audit**. De nieuwste rij heeft status **ok** (geen upstream-fout).
2. Detail → vier tabs:
   - **Origineel** — samengestelde opdracht (template + dossier), plaintext
     PII (BR-G01).
   - **Pseudonimized (naar LLM)** — wat upstream ontving.
   - **Response (pseud)** — ruwe upstream-response (JSON).
   - **Response (terug)** — na TWO_WAY-terugvertaling; hier gelijk aan
     pseud (geen TWO_WAY op dit template).
3. Op **Home** (Uitgebreid): mapping-tabel met `[1w]`-badges, curl-equivalent
   en raw upstream-body.

---

## Scenario 2 — Manual review via 423-flow

**Doel.** HTTP **423** wanneer detectie entiteiten onder de confidence-
threshold zet; beslissing in de review-queue; hervatten met
`resume_session` in de body.

### API-contract (curl)

```bash
curl -i -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "dossier": "Vraag van Pietersen: kan dit door?"
  }'
```

Bij twijfel detectie:

- HTTP **423 Locked**.
- Body (voorbeeld):
  ```json
  {
    "session_id": "<hex>",
    "review_required": true,
    "review_item_ids": [42],
    "review_url": "/ui/review?session=<hex>",
    "message": "Detectie-confidence ligt onder de threshold; …"
  }
  ```

Noteer `session_id`. Open de queue via sidebar **Review Queue**, of deeplink
`http://localhost:8501/Review_Queue?session=<hex>` (Streamlit leest
`?session=`; het API-veld `review_url` is een legacy pad-hint).

> **Let op (handmatige demo):** met de huidige NAME-detectie (DEDUCE +
> rol-heuristiek) levert dit dossier vaak **HTTP 200** i.p.v. 423 — de
> naam wordt met hoge confidence herkend. Het 423-contract blijft gedekt
> door `tests/test_proxy.py` (gemockte lage confidence). Voor een
> handmatige 423-run: gebruik **Home → Start** met een dossier waar de
> dry-run **twijfel-entiteiten** toont, klik **Open openstaande
> beslissingen**, beslis in de queue, en hervat daarna via curl of
> **Hervat naar extern LLM**.

### Beslis in de UI

1. Open **Review Queue**. Kies de sessie (`… (1 openstaand)`).
2. Lees de snippet; pending match is gemarkeerd in oranje.
3. **Accept**, **Modify** (ander type) of **Reject**.
4. Na accept/modify/reject van alle items: **Hervat sessie**-paneel met JSON:
   ```json
   {
     "template_id": 1,
     "dossier": "…",
     "resume_session": "<hex>"
   }
   ```

### Hervat de sessie

```bash
curl -sS -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "dossier": "Vraag van Pietersen: kan dit door?",
    "resume_session": "<hex>"
  }' | jq .
```

- HTTP **200**; geaccepteerde entiteiten staan als `[PER-xxxxxx]` upstream.
- **Audit:** de **eerste** 423-call schrijft **geen** audit-rij. Pas na
  succesvolle hervat (of upstream-fout na review) verschijnt een rij met
  status **ok** (of **error** bij upstream-fout) en hetzelfde `session_id`.

---

## Scenario 3 — Mixed ONE_WAY / TWO_WAY-overrides

**Doel.** Alleen `NAME` als `TWO_WAY`; rest blijft `ONE_WAY`. Naam komt
terug in de response; BSN blijft pseudoniem.

### Maak het template aan via de UI

1. **Opdrachten** → tab **Bewerken** → **+ Nieuwe opdracht**.
2. Vul in:
   - Groep: `demo`
   - Naam: `mixed-overrides`
   - Provider: `anthropic`, Model: `claude-opus-4-8` (UI-default)
   - Opdrachttekst: `Vat de zin samen: {input}`
   - Max tokens: `256`
   - Template-default modus: leeg (→ super-default ONE_WAY)
   - Overrides: rij `NAME` → **TWO_WAY**
   - Two-way-onderbouwing: `Naam mag in dialoog terugkomen omdat de
     gebruiker dezelfde persoon meerdere keren noemt.`
3. **Opslaan**. Noteer `id` (overzicht-tab of Uitgebreid op Home).

### Stuur de opdracht

```bash
curl -sS -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": <id-van-zojuist>,
    "dossier": "Mevrouw Pietersen (BSN 123456782) belt over haar afspraak."
  }' | jq .
```

### Wat je ziet

Audit-detail:

- **Pseudonimized (naar LLM)** — `Mevrouw [PER-xxxxxx] (BSN [BSN-yyyyyy]) …`
- **Response (pseud)** — Claude gebruikt dezelfde pseudoniemen.
- **Response (terug)** — `[PER-xxxxxx]` → **Pietersen**; BSN blijft
  `[BSN-yyyyyy]`.

Op **Home** (Uitgebreid): mapping `[2w]` op `NAME`, `[1w]` op `BSN`.

---

## Wat dit alles bewijst

| Aspect | Scenario | Hoe je het ziet |
| --- | --- | --- |
| Pseudonimisering happy-path | 1 | Origineel ≠ Pseudonimized in audit |
| Review-routing bij lage confidence | 2 | HTTP 423 + queue + resume (contract; zie pytest) |
| Drie-laagse modus-resolutie | 3 | `[2w]` op één type, `[1w]` op de rest |
| DB-separatie (BR-G02) | alle | Geen vault-mappings in audit-rijen |
| Append-only audit (BR-G01) | alle | Elke voltooide upstream-call = nieuwe rij |

Automatische verificatie: `uv run pytest tests/ -v`.

---

## Acceptatie v0.3 (praktijktests)

De v0.3-vlag vereist de drie flows op een **verse DB** (zie
[PLAN.md](PLAN.md) §19). Onderstaande tabel koppelt elk scenario aan
proxy-integratietests en handmatige UI-stappen.

| Scenario | DEMO | Automatisch (`tests/test_proxy.py`) | Handmatig (UI) |
| --- | --- | --- | --- |
| 1 — happy path | § Scenario 1 | `test_clean_prompt_roundtrip_pseudonymizes_and_returns_response` | Home Uitgebreid: mapping + audit-tabs |
| 2 — review 423 | § Scenario 2 | `test_low_confidence_returns_423_and_enqueues_review`, `test_resume_after_accepting_review_succeeds` | Review Queue + hervat (indien dry-run pending) |
| 3 — TWO_WAY mix | § Scenario 3 | `test_two_way_override_depseudonymizes_response` | Opdrachten: NAME `[2w]` + audit detail |

**Snelle acceptatie-run** (offline, tmp-DB per test):

```bash
uv run pytest tests/test_proxy.py::test_clean_prompt_roundtrip_pseudonymizes_and_returns_response \
  tests/test_proxy.py::test_low_confidence_returns_423_and_enqueues_review \
  tests/test_proxy.py::test_resume_after_accepting_review_succeeds \
  tests/test_proxy.py::test_two_way_override_depseudonymizes_response -v
```

**Handmatige walkthrough:** proxy (:8080) + Streamlit (:8501),
`ANTHROPIC_API_KEY`, minimaal één opdracht met `{input}`. Laag 3 (Ollama)
niet vereist.
