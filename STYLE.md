# Pylades — stijlgids

Deze gids beschrijft hoe we code, UI en documentatie schrijven. Doel: leesbare, auditeerbare software zonder verrassingen voor reviewers en bijdragers.

Zie ook [CONTRIBUTING.md](CONTRIBUTING.md) voor het bijdrageproces.

---

## Taal en toon

| Context | Taal | Voorbeeld |
| --- | --- | --- |
| UI-labels, meldingen, help-tekst | **Nederlands** | "Wacht op jouw beoordeling" |
| Code (identifiers, types, enums) | **Engels** | `EntityType`, `ReviewStatus` |
| Module-/functiedocstrings | **Nederlands** | Waarom en gedrag, niet alleen wat |
| README, PLAN, DEMO, CONTRIBUTING | **Nederlands** | Doelgroep: zorg + tech in NL |
| Commit messages | **Engels of Nederlands** | Kort, actief, waarom centraal |

**Toon in UI:** direct en geruststellend voor niet-technische gebruikers. Geen jargon zonder uitleg in Eenvoudig-modus. In Uitgebreid-modus mag technische detail wel.

**Toon in code-review:** respectvol, concreet. Vraag om verduidelijking in plaats van aannames.

---

## Python

### Tooling

- **Formatter/linter:** [Ruff](https://docs.astral.sh/ruff/) (`uv run ruff check .`, `uv run ruff format .`)
- **Regels:** zie `[tool.ruff]` in [pyproject.toml](pyproject.toml) — regellengte **100**, target **Python 3.11+**
- **Type-check:** mypy strict op `shared/` en `proxy/` (`uv run mypy shared proxy`)
- **Tests:** pytest (`uv run pytest`); nieuwe gedrag hoort tests te hebben

### Stijl

- Dubbele quotes (`quote-style = "double"`)
- `from __future__ import annotations` bovenaan nieuwe modules
- Type hints op publieke functies; in `shared/` en `proxy/` volledig getypeerd (mypy strict)
- Prefer `StrEnum` voor waarden die in DB/JSON als string leven
- Pydantic v2 voor settings en request/response-modellen
- Geen bare `except:`; log met context (`logger.warning(..., exc_info=...)` waar passend)

### Module-docstrings

Elke package en elk niet-triviaal bestand begint met een korte docstring: **rol**, **belangrijkste invariant**, **link naar tests** waar relevant.

```python
"""FastAPI-proxy voor `POST /v1/messages`.

Orkestreert detect → review → generalize → pseudonymize → upstream → audit.
Tests in `tests/test_proxy.py` controleren elke takking.
"""
```

### Imports

- Standaardvolgorde: stdlib → third party → lokaal (`shared`, `proxy`, `ui`)
- Ruff `I` (isort) handhaaft dit
- **Uitzondering Streamlit-pagina's:** `sys.path`-shim vóór project-imports — zie [ui/views/0_Home.py](ui/views/0_Home.py); Ruff negeert `E402` daar bewust

### Package-indeling

| Package | Verantwoordelijkheid |
| --- | --- |
| `shared/` | Config, crypto, DB-init, domeinmodellen — **geen** FastAPI/Streamlit |
| `proxy/` | Detectie, pseudonimisering, FastAPI-app, audit, templates-service |
| `ui/` | Streamlit-shell, helpers, theme; pagina's in `ui/views/` |
| `data/` | Fixtures en fictieve testdata |
| `tests/` | Spiegelt `proxy/` en `shared/`; UI via helpers waar nodig |

**Leaf-regel:** `shared/models.py` importeert niets uit andere `shared`-modules — domeinvocabulaire blijft dependency-vrij.

---

## Geheimen en privacy

- **Nooit** API-keys, tokens of `global_secret.bin` in git
- Configuratie via `.env` (lokaal) en [`.env.example`](.env.example) (placeholder, geen echte waarden)
- Default voor `anthropic_api_key` in code: lege string `""`
- Templates en testdata: **fictief**, geen herleidbare persoonsgegevens
- Geen PII in logs, commit messages of issue-screenshots

Zie [.gitignore](.gitignore): `*.db`, `secrets/*`, `.env`, `logs/`.

---

## Versienummer

- **Bron van waarheid:** [shared/version.py](shared/version.py) → `__version__`
- **Sync:** [pyproject.toml](pyproject.toml) `[project].version` moet gelijk zijn (`tests/test_version.py`)
- UI-titels: importeer `pylades_display`, `pylades_page_title`, `version_display` — **geen** hardcoded `v0.x.y` in Python
- Spec-/milestoneversie (`TARGET_VERSION`, `v0.3` in PLAN) is documentatiescope, niet per se de huidige release

---

## Streamlit UI

### Pagina's

- Entry: [ui/Home.py](ui/Home.py) — bootstrap, navigatie, theme
- Views: [ui/views/](ui/views/) — genummerde bestandsnamen (`0_Home.py`, `1_Status.py`, …)
- Elke pagina roept `apply_polish()` aan (via shared bootstrap) voor CSS uit [ui/theme.py](ui/theme.py)

### Modi

- **Eenvoudig** — plain language, samenvattingsstrip, entiteit-kaartjes, bewuste bevestiging vóór upstream
- **Uitgebreid** — diagnostics, JSON, mapping-tabel, curl-equivalent

Eén gedeelde pijplijn; alleen presentatie verschilt. Logica hoort in helpers (`ui/testrun_helpers.py`, `ui/review_flow.py`), niet gedupliceerd in views.

### Kleuren

Twee lagen — **niet door elkaar halen:**

1. **Merk-accent** `#F97315` — knoppen, sidebar, primary actions (Streamlit `primaryColor`)
2. **Status-semantiek** — groen / blauw / geel / rood op inhoud

| Rol | Hex | Gebruik |
| --- | --- | --- |
| OK / ONE_WAY | `#2E7D32` | Beschermd, successtrip, LLM-antwoord |
| TWO_WAY | `#256F8A` | Beschermd + terug vertaald |
| Pending | `#D4A017` | Review nodig, HTTP 423 |
| Fout | `#A0263A` | Proxy/HTTP-fout |

Constanten en CSS: `ui/theme.py`. Uitgebreide tabel: [.cursor/rules/ui-color-status.mdc](.cursor/rules/ui-color-status.mdc).

**Niet doen:** oranje op status-strips; rood als merkkleur; nieuwe statuskleuren zonder STYLE + theme bij te werken.

### Streamlit-config

[.streamlit/config.toml](.streamlit/config.toml): dark theme, `toolbarMode = "viewer"`, geen sidebar auto-nav (MPA via `st.navigation`).

---

## Tests

- Offline en deterministisch: mock upstream (`httpx.MockTransport`), temp DB's via `tmp_path` + `monkeypatch` op `settings`
- Fixture `proxy_env` / vergelijkbaar pattern in [tests/test_proxy.py](tests/test_proxy.py)
- Naamgeving: `test_<gedrag>_<verwachting>`
- Geen echte Anthropic-calls in CI
- Assert op gedrag en contract (HTTP-status, geen PII in upstream-body), niet op implementatiedetail tenzij security-kritisch

---

## Documentatie

- Productgedrag en architectuur: README, PLAN, DEMO, SPEC
- **Niet** vermelden welke LLM/IDE code heeft gegenereerd (Copilot, Cursor, enz.)
- **Niet** "prompt om code te genereren" in docs opnemen
- **Wel** documenteren: Anthropic als upstream-provider, proxy-contract, client-apps die via Pylades praten
- Spec-bestanden: neutrale namen (`SPEC-v0.3.md`), geen `cursor-prompt-*`

Wijzigingen aan publieke API of UI-flow: README of DEMO meenemen als het gebruikers raakt.

---

## Git en pull requests

- Kleine, logische commits
- PR beschrijft **wat** en **waarom**
- Eerste PR: CLA via CLA Assistant (zie [CLA.md](CLA.md))
- Vóór merge: `uv run pytest`, `uv run ruff check .`, relevante mypy

---

## Checklist bij nieuwe code

- [ ] Ruff clean; types kloppen in `shared/` / `proxy/`
- [ ] Tests voor nieuw gedrag of bugfix
- [ ] Geen secrets of PII in diff
- [ ] UI-tekst Nederlands; statuskleuren uit `ui/theme.py`
- [ ] Versie-strings via `shared.version` indien zichtbaar voor gebruiker
- [ ] Docs bijgewerkt als gedrag voor gebruikers verandert
