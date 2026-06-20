# Pylades — Demo

> Doelversie v0.3 — demo-scenario's voor acceptatie; releaseversie staat in
> `shared/version.py`.

Drie reproduceerbare scenario's die samen de complete pijplijn raken:
clean round-trip, manual review, en gemengde modus-overrides. Geschreven
voor iemand die de proxy lokaal draait en voor het eerst wil bekijken
wat Pylades doet.

> Voorwaarde: `uv sync --extra dev` is uitgevoerd, `ANTHROPIC_API_KEY`
> staat in `.env`, en DEDUCE is beschikbaar (standaard via `uv sync`).
> Volg `README.md` § Installatie als dit nog niet klopt.

## 0. Start

In twee terminals:

```bash
# Terminal 1 — proxy op :8080
uv run uvicorn proxy.main:app --port 8080

# Terminal 2 — UI op :8501
uv run streamlit run ui/Home.py
```

Open <http://localhost:8501> in een browser. Je landt
direct op de testrun-pagina (Home). Controleer dat alle vier
status-kaartjes op de **Status**-pagina (menu-optie 2) groen staan
(proxy, Ollama-optioneel, DEDUCE, databases).

---

## Scenario 1 — Clean round-trip

**Doel.** Eén request met een BSN doorvoeren zonder dat er review-items
ontstaan en zien dat de response gewoon terugkomt met pseudoniemen
op de plek waar de BSN stond.

### Via de UI (Compact)

1. Open **Home**. De testrun-pagina is de homepagina; het
   statusoverzicht staat onder menu-optie **Status**.
2. Bovenaan staat een modus-schakelaar: **Compact** is default,
   **Uitgebreid** toont alle diagnostics (curl, raw upstream-body,
   mapping-tabel, JSON-response).
3. Kies template id `1` (de seed-template uit `data/fixtures.py` voldoet
   met `"Vat dit patiëntdossier samen: {input}"`).
4. Plak in het dossier-veld:
   `"Patiënt heeft BSN 123456782 en woont op 1011AB."`
5. Klik **Start** (veilige voorbeeld-analyse — er gaat nog
   niets richting het externe LLM).
6. Lees de samenvattingskaart en de entiteit-kaartjes. Klik daarna
   bewust **Verstuur naar extern LLM**.

### Via curl

```bash
curl -sS -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "dossier": "Patiënt heeft BSN 123456782 en woont op 1011AB."
  }' | jq .
```

### Wat je ziet

- HTTP 200, response-header `X-Pylades-Session: <uuid-hex>`.
- De response-body bevat de assistant-tekst van Claude. Omdat het
  template de super-default `ONE_WAY` gebruikt, blijven pseudoniemen als
  `[BSN-abc123]` en `[PC6-de4567]` in de response staan; Pylades vertaalt
  ze niet terug.

### Bevestig in de UI

1. Open **Audit**. Bovenaan staat de zojuist binnengekomen rij met
   status `ok`.
2. Klik in het detail-overzicht op het entry-id. De vier tabs tonen:
   - **Origineel** — je samengestelde opdracht (template-opdracht +
     dossier) met BSN en postcode plaintext.
   - **Pseudonimized** — dezelfde tekst, maar BSN en PC6 vervangen door
     pseudoniemen.
   - **Response (pseud)** — wat Claude letterlijk terugschreef.
   - **Response (terug)** — identiek aan "pseud" (geen TWO_WAY in dit
     template, dus niets om terug te vertalen).
3. Op **Home** (Uitgebreid) zie je dezelfde mapping-tabel met de twee
   entiteiten en `[1w]`-badge, plus de curl-equivalent en de raw
   upstream-body die naar het externe LLM ging.

---

## Scenario 2 — Manual review via 423-flow

**Doel.** Laat detectie expres twijfelen door een naam zonder context,
los het op via de Review-queue, hervat de sessie.

### Stuur een twijfelachtige opdracht

```bash
curl -i -X POST http://localhost:8080/v1/messages \
  -H "Content-Type: application/json" \
  -d '{
    "template_id": 1,
    "dossier": "Vraag van Pietersen: kan dit door?"
  }'
```

### Wat je ziet

- HTTP **423 Locked**.
- Response-body:
  ```json
  {
    "session_id": "<hex>",
    "review_required": true,
    "review_item_ids": [42],
    "review_url": "/ui/review?session=<hex>",
    "message": "Detectie-confidence ligt onder de threshold; …"
  }
  ```

Noteer het `session_id`.

### Beslis in de UI

1. Open **Review-queue**. De sessie staat bovenaan met "1 openstaand".
2. Selecteer hem, lees de snippet met `Pietersen` in oranje.
3. Klik **Accept** (of **Modify** als je het type wilt wijzigen, of
   **Reject** als het géén PII is).
4. De pagina toont nu het Hervat-paneel met een JSON-snippet:
   ```json
   {
     "template_id": 1,
     "dossier": "Vraag van Pietersen: kan dit door?",
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

Nu antwoordt de proxy met HTTP 200 en de geaccepteerde naam is in de
upstream opdracht vervangen door `[PER-xxxxxx]`. In **Audit** verschijnt
een rij met status `ok` en hetzelfde `session_id` als de eerste 423-call.

---

## Scenario 3 — Mixed ONE_WAY / TWO_WAY-overrides

**Doel.** Een template waarin alleen `NAME` als TWO_WAY is geconfigureerd
en al het andere ONE_WAY blijft. Demonstreert selectieve
de-pseudonimisering: de naam komt netjes terug in de response, het BSN
blijft als pseudoniem staan.

### Maak het template aan via de UI

1. Open **Opdrachten** → tab **Bewerken**, kies **+ Nieuwe opdracht**.
2. Vul in:
   - Groep: `demo`
   - Naam: `mixed-overrides`
   - Provider: `anthropic`, Model: `claude-opus-4-7`
   - Opdrachttekst: `Vat de zin samen: {input}`
   - Max tokens: `256`
   - Template-default modus: laat leeg (→ super-default ONE_WAY).
   - In de overrides-tabel: zet rij `NAME` op **TWO_WAY**.
   - Two-way-onderbouwing: `Naam mag in dialoog terugkomen omdat de
     gebruiker dezelfde persoon meerdere keren noemt.`
3. Klik **Opslaan**. Noteer het toegekende `id` (zichtbaar in de
   overzichts-tab).

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

Audit-detail laat exact zien wat er gebeurt:

- **Pseudonimized (naar LLM)** — `Mevrouw [PER-xxxxxx] (BSN [BSN-yyyyyy])
  belt over haar afspraak.`
- **Response (pseud)** — Claude antwoordt met `Mevrouw [PER-xxxxxx] …
  [BSN-yyyyyy] …`.
- **Response (terug)** — exact dezelfde tekst, maar `[PER-xxxxxx]` is
  terugvertaald naar **Pietersen**; de BSN blijft als pseudoniem staan.

Op **Home** (Uitgebreid) toont de mapping-tabel de twee entiteiten
met `[2w]` op `NAME` en `[1w]` op `BSN` — bewijs dat de drie-laagse
resolver de per-entity override correct toepaste boven super-default.

---

## Wat dit alles bewijst

| Aspect | Scenario | Hoe je het ziet |
| --- | --- | --- |
| Pseudonimisering happy-path | 1 | Origineel ≠ Pseudonimized in audit |
| Review-routing bij lage confidence | 2 | HTTP 423 + manual decision + resume |
| Drie-laagse modus-resolutie | 3 | `[2w]` op één type, `[1w]` op de rest |
| DB-separatie (BR-G02) | alle | Vault-mappings nooit zichtbaar in audit-rijen |
| Append-only audit (BR-G01) | alle | Elke call krijgt een nieuwe rij |

Voor automatische verificatie van diezelfde garanties: `uv run pytest
tests/ -v`. De volledige suite hoort groen te zijn.

---

## Acceptatie v0.3 (praktijktests)

De v0.3-vlag vereist de drie flows hierboven op een **verse DB** (zie
[PLAN.md](PLAN.md) §19). Onderstaande tabel koppelt elk DEMO-scenario aan
automatische proxy-integratietests en handmatige UI-stappen.

| Scenario | DEMO | Automatisch (`tests/test_proxy.py`) | Handmatig (UI) |
| --- | --- | --- | --- |
| 1 — happy path | § Scenario 1 | `test_clean_prompt_roundtrip_pseudonymizes_and_returns_response` | Home Uitgebreid: mapping + audit-tabs |
| 2 — review 423 | § Scenario 2 | `test_low_confidence_returns_423_and_enqueues_review`, `test_resume_after_accepting_review_succeeds` | Review-queue Accept + hervat-curl |
| 3 — TWO_WAY mix | § Scenario 3 | `test_two_way_override_depseudonymizes_response` | Opdrachten: NAME override `[2w]` + audit detail |

**Snelle acceptatie-run** (offline, verse tmp-DB per test):

```bash
uv run pytest tests/test_proxy.py::test_clean_prompt_roundtrip_pseudonymizes_and_returns_response \
  tests/test_proxy.py::test_low_confidence_returns_423_and_enqueues_review \
  tests/test_proxy.py::test_resume_after_accepting_review_succeeds \
  tests/test_proxy.py::test_two_way_override_depseudonymizes_response -v
```

**Volledige handmatige walkthrough:** start proxy (:8080) + Streamlit (:8501),
zet `ANTHROPIC_API_KEY`, doorloop Scenario 1–3 met de curl/UI-stappen hierboven.
Laag 3 (Ollama) is **niet** vereist — default `use_llm=False`.
