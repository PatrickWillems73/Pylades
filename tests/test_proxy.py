"""Integratietests voor `proxy/main.py`.

`httpx.ASGITransport` mount de FastAPI-app zonder uvicorn; `MockTransport`
fakes Anthropic. Tests blijven offline, deterministisch en snel.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

import proxy.main as main_mod
from proxy.audit import get_logs_by_session, get_recent_logs
from proxy.main import app
from proxy.review import decide, get_pending
from proxy.templates import upsert_template
from shared.config import settings
from shared.db import get_content_connection, init_databases
from shared.models import (
    DetectionLayer,
    DetectionResult,
    Entity,
    EntityType,
    PseudonymizationMode,
    ReviewStatus,
    Template,
)


@pytest.fixture
def proxy_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")
    init_databases()


def _make_template(
    *,
    llm_provider: str = "anthropic",
    llm_naam: str = "claude-sonnet-4-5",
    prompt_tekst: str = "Anonimiseer en vat samen: {input}",
    max_tokens: int = 32,
    use_llm: bool = False,
    default_mode: PseudonymizationMode | None = None,
    mode_overrides: dict[EntityType, PseudonymizationMode] | None = None,
    two_way_justification: str | None = None,
) -> int:
    """Maak een template in de content-db en lever zijn id terug."""
    return upsert_template(
        Template(
            groep="test",
            naam="t",
            llm_provider=llm_provider,
            llm_naam=llm_naam,
            prompt_tekst=prompt_tekst,
            max_tokens=max_tokens,
            use_llm=use_llm,
            default_mode=default_mode,
            mode_overrides=mode_overrides or {},
            two_way_justification=two_way_justification,
        )
    )


def _anthropic_response(text: str) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


@pytest.fixture
def mock_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Any]]:
    """Maakt een upstream-mock die het laatst-gestuurde request bewaart.

    Test bepaalt het responder-gedrag via `state['responder']`.
    """
    state: dict[str, Any] = {
        "last_request_body": None,
        "responder": lambda req_body: _anthropic_response("Default mock antwoord"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        state["last_request_body"] = json.loads(request.content.decode("utf-8"))
        responder: Callable[[dict[str, Any]], dict[str, Any] | httpx.Response] = state["responder"]
        result = responder(state["last_request_body"])
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    def fake_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(main_mod, "_create_upstream_client", fake_factory)
    yield state


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://pylades.test",
    )


async def test_healthz(proxy_env: None) -> None:
    async with await _client() as c:
        r = await c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_lifespan_initialises_databases_on_first_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressie: bij verse install moet de FastAPI-lifespan de DBs opzetten,
    zodat `uvicorn proxy.main:app` op een schone machine niet crasht op
    'no such table: config' bij de eerste request."""
    monkeypatch.setattr(settings, "content_db_path", tmp_path / "c.db")
    monkeypatch.setattr(settings, "vault_db_path", tmp_path / "v.db")
    monkeypatch.setattr(settings, "global_secret_path", tmp_path / "sec.bin")

    assert not settings.content_db_path.exists()
    assert not settings.vault_db_path.exists()

    async with main_mod._lifespan(app):
        with get_content_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert {"templates", "audit_log", "config", "sessions", "review_queue"} <= tables
        assert settings.vault_db_path.exists()


# ---------------------------------------------------------------------------
# Validatie van het nieuwe body-contract
# ---------------------------------------------------------------------------


async def test_missing_template_id_returns_422(proxy_env: None) -> None:
    body = {"dossier": "iets"}
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)
    assert r.status_code == 422


async def test_missing_dossier_returns_422(proxy_env: None) -> None:
    body = {"template_id": 1}
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)
    assert r.status_code == 422


async def test_empty_dossier_returns_422(proxy_env: None) -> None:
    template_id = _make_template()
    body = {"template_id": template_id, "dossier": ""}
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)
    assert r.status_code == 422


async def test_unknown_template_id_returns_404(proxy_env: None) -> None:
    body = {"template_id": 999_999, "dossier": "x"}
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)
    assert r.status_code == 404
    assert "bestaat niet" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_clean_prompt_roundtrip_pseudonymizes_and_returns_response(
    proxy_env: None,
    mock_upstream: dict[str, Any],
) -> None:
    """BSN moet pseudonimized worden vóór Anthropic, en pseudoniemen komen terug."""
    template_id = _make_template()

    def responder(req: dict[str, Any]) -> dict[str, Any]:
        echo = req["messages"][0]["content"]
        return _anthropic_response(f"Reactie verwijst naar {echo.split()[-1]}.")

    mock_upstream["responder"] = responder

    body = {
        "template_id": template_id,
        "dossier": "Patient Pietersen heeft BSN 123456782.",
    }
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)

    assert r.status_code == 200, r.text
    sent_to_anthropic = mock_upstream["last_request_body"]["messages"][0]["content"]
    assert "123456782" not in sent_to_anthropic
    assert "[BSN-" in sent_to_anthropic
    # Server-side stelt het model + max_tokens uit de template in.
    assert mock_upstream["last_request_body"]["model"] == "claude-sonnet-4-5"
    assert mock_upstream["last_request_body"]["max_tokens"] == 32

    response_text = r.json()["content"][0]["text"]
    assert "[" in response_text  # super-default ONE_WAY → pseudoniem blijft staan

    session_id = r.headers.get("X-Pylades-Session")
    assert session_id is not None
    logs = get_logs_by_session(session_id)
    assert len(logs) == 1
    audit = logs[0]
    assert "123456782" in audit.original_prompt
    assert "123456782" not in audit.pseudonymized_prompt


# ---------------------------------------------------------------------------
# Review-flow
# ---------------------------------------------------------------------------


def _detect_substring_as_name(
    needle: str, *, confidence: float
) -> Callable[[str], DetectionResult]:
    """Geef een `detect_all`-mock terug die `needle` als NAME-entity vindt.

    Werkt dynamisch op de samengestelde prompt (template-prefix + dossier),
    zodat de spans kloppen ongeacht hoe lang het template-voorvoegsel is.
    """

    def fake_detect_all(text: str, **_kwargs: Any) -> DetectionResult:
        idx = text.find(needle)
        if idx < 0:
            return DetectionResult()
        ent = Entity(
            original=needle,
            entity_type=EntityType.NAME,
            confidence=confidence,
            detection_layer=DetectionLayer.SPACY,
            start=idx,
            end=idx + len(needle),
        )
        if confidence < 0.7:
            return DetectionResult(confident_entities=[], pending_review=[ent])
        return DetectionResult(confident_entities=[ent], pending_review=[])

    return fake_detect_all


async def test_low_confidence_returns_423_and_enqueues_review(
    proxy_env: None,
    mock_upstream: dict[str, Any],  # noqa: ARG001 — niet aangeroepen, maar verbinden voor isolation
) -> None:
    template_id = _make_template()

    monkeypatch_ = pytest.MonkeyPatch()
    monkeypatch_.setattr(
        main_mod, "detect_all", _detect_substring_as_name("De Boer", confidence=0.5)
    )
    try:
        body = {"template_id": template_id, "dossier": "Mevr De Boer."}
        async with await _client() as c:
            r = await c.post("/v1/messages", json=body)
    finally:
        monkeypatch_.undo()

    assert r.status_code == 423
    payload = r.json()
    assert payload["review_required"] is True
    assert "session_id" in payload
    pending = get_pending(payload["session_id"])
    assert len(pending) == 1
    assert pending[0].detected_text == "De Boer"


async def test_resume_after_accepting_review_succeeds(
    proxy_env: None,
    mock_upstream: dict[str, Any],
) -> None:
    """1) detect-mock retourneert pending; 423 + enqueue. 2) accept; 3) resume → 200."""
    template_id = _make_template()

    mock_upstream["responder"] = lambda req: _anthropic_response("Klaar.")  # noqa: ARG005

    monkeypatch_ = pytest.MonkeyPatch()
    monkeypatch_.setattr(
        main_mod, "detect_all", _detect_substring_as_name("De Boer", confidence=0.5)
    )
    try:
        body = {"template_id": template_id, "dossier": "Mevr De Boer."}
        async with await _client() as c:
            r1 = await c.post("/v1/messages", json=body)
        assert r1.status_code == 423
        session_id = r1.json()["session_id"]

        pending = get_pending(session_id)
        decide(pending[0].id or 0, ReviewStatus.ACCEPTED)

        resume_body = {**body, "resume_session": session_id}
        async with await _client() as c:
            r2 = await c.post("/v1/messages", json=resume_body)
    finally:
        monkeypatch_.undo()

    assert r2.status_code == 200, r2.text
    assert mock_upstream["last_request_body"] is not None
    sent = mock_upstream["last_request_body"]["messages"][0]["content"]
    assert "De Boer" not in sent
    assert "[PER-" in sent


# ---------------------------------------------------------------------------
# Provider-gating
# ---------------------------------------------------------------------------


async def test_use_llm_flag_routes_to_detect_all_kwarg(
    proxy_env: None,
    mock_upstream: dict[str, Any],
) -> None:
    """Template.use_llm=True → proxy roept detect_all(..., use_llm=True) aan.

    Bewijst dat de per-template-schakelaar voor laag 3 (Ollama) feitelijk
    aankomt bij de detectie-orkestratie. Laag-2/3-stubs worden buiten dit pad
    getest; we valideren hier alleen de bedrading.
    """
    template_id_off = _make_template(use_llm=False)
    template_id_on = _make_template(use_llm=True)
    mock_upstream["responder"] = lambda req: _anthropic_response("ok")  # noqa: ARG005

    captured: list[bool] = []

    def spy(_text: str, *, use_llm: bool = False, **_kwargs: Any) -> DetectionResult:
        # Bewust géén echte detectie: we testen alleen de bedrading van de
        # template-flag naar `detect_all`. De spy retourneert leeg zodat
        # eventueel lokaal draaiende Ollama de test niet onbetrouwbaar maakt.
        captured.append(use_llm)
        return DetectionResult(confident_entities=[], pending_review=[])

    monkeypatch_ = pytest.MonkeyPatch()
    monkeypatch_.setattr(main_mod, "detect_all", spy)
    try:
        async with await _client() as c:
            r1 = await c.post(
                "/v1/messages",
                json={"template_id": template_id_off, "dossier": "x"},
            )
            r2 = await c.post(
                "/v1/messages",
                json={"template_id": template_id_on, "dossier": "x"},
            )
    finally:
        monkeypatch_.undo()

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert captured == [False, True]


async def test_non_anthropic_provider_returns_501(
    proxy_env: None,
    mock_upstream: dict[str, Any],  # noqa: ARG001
) -> None:
    template_id = _make_template(llm_provider="openai", llm_naam="gpt-4")
    body = {"template_id": template_id, "dossier": "ok"}
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)
    assert r.status_code == 501
    assert "anthropic" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Upstream-faal en TWO_WAY
# ---------------------------------------------------------------------------


async def test_upstream_error_is_audited_and_propagated(
    proxy_env: None,
    mock_upstream: dict[str, Any],
) -> None:
    template_id = _make_template()

    def fail_responder(req: dict[str, Any]) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(429, json={"type": "error", "error": {"type": "rate_limit"}})

    mock_upstream["responder"] = fail_responder

    body = {"template_id": template_id, "dossier": "BSN 123456782"}
    async with await _client() as c:
        r = await c.post("/v1/messages", json=body)

    assert r.status_code == 429
    recent = get_recent_logs(limit=1)
    assert len(recent) == 1
    assert recent[0].error is not None
    assert "429" in recent[0].error
    assert "123456782" in recent[0].original_prompt
    assert "123456782" not in recent[0].pseudonymized_prompt


async def test_two_way_override_depseudonymizes_response(
    proxy_env: None,
    mock_upstream: dict[str, Any],
) -> None:
    """NAME → TWO_WAY override op de template: pseudoniem in response wordt terugvertaald."""
    template_id = _make_template(
        mode_overrides={EntityType.NAME: PseudonymizationMode.TWO_WAY},
        two_way_justification="case-study analysis requires names",
    )

    name_entity = Entity(
        original="Pietersen",
        entity_type=EntityType.NAME,
        confidence=0.99,
        detection_layer=DetectionLayer.SPACY,
        start=0,
        end=9,
    )

    def fake_detect_all(text: str, **_kwargs: Any) -> DetectionResult:
        # Detectie-positie hangt af van de prompt-samenstelling; we matchen
        # gewoon op aanwezigheid van "Pietersen" en geven daar één entity terug.
        idx = text.find("Pietersen")
        if idx < 0:
            return DetectionResult()
        return DetectionResult(
            confident_entities=[
                name_entity.model_copy(update={"start": idx, "end": idx + len("Pietersen")})
            ],
            pending_review=[],
        )

    def echo_responder(req: dict[str, Any]) -> dict[str, Any]:
        return _anthropic_response(req["messages"][0]["content"])

    mock_upstream["responder"] = echo_responder

    monkeypatch_ = pytest.MonkeyPatch()
    monkeypatch_.setattr(main_mod, "detect_all", fake_detect_all)
    try:
        body = {"template_id": template_id, "dossier": "Patient Pietersen lacht."}
        async with await _client() as c:
            r = await c.post("/v1/messages", json=body)
    finally:
        monkeypatch_.undo()

    assert r.status_code == 200, r.text
    response_text = r.json()["content"][0]["text"]
    assert "Pietersen" in response_text
    assert "[PER-" not in response_text
