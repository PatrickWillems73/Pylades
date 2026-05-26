"""FastAPI-proxy voor `POST /v1/messages` (BR-G01 + BR-A04 + BR-C06).

Orkestreert: detect → review-poort → generalize → pseudonymize → upstream
call → depseudonymize → audit. De volgorde is significant en wordt door
het type-systeem niet afgedwongen; één misplaatste lijn lekt PII naar
Anthropic. Tests in `tests/test_proxy.py` controleren elke takking.

V0.3-scope (zie PLAN §15a):
- Eigen body-contract `{template_id, dossier, resume_session}` — *geen*
  Anthropic-pass-through meer.
- Prompt-template is verplichte API-parameter; server stelt de prompt
  zelf samen via `{input}`-substitutie.
- `model`, `provider` en `max_tokens` komen uitsluitend uit de template.
- Anthropic-only (`llm_provider != "anthropic"` → 501); block-content
  staat in v1.0.
- Session-id is UUID4-hex; resume via body-veld `resume_session`.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from statistics import mean
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from proxy.audit import log_request
from proxy.detection import detect_all
from proxy.generalization import generalize_all
from proxy.mapping import PseudonymManager
from proxy.pseudonymization import depseudonymize, pseudonymize
from proxy.review import all_resolved, enqueue, get_accepted_entities
from proxy.templates import get_template
from shared.config import settings
from shared.db import get_content_connection, init_databases
from shared.models import DetectionResult, Entity, ReviewStatus, Template
from shared.version import __version__, pylades_display, target_version_display

logger = logging.getLogger(__name__)


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_UPSTREAM_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Upstream-client factory (test-overschrijfbaar)
# ---------------------------------------------------------------------------


def _create_upstream_client() -> httpx.AsyncClient:
    """Eén centrale fabriek zodat tests een MockTransport injecteren.

    De test-suite vervangt deze functie via monkeypatch; FastAPI's eigen
    dependency-systeem is voor deze ene factory overkill omdat de client
    binnen een request-scope geopend en gesloten moet worden.
    """
    return httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Initialiseer de databases bij startup (idempotent).

    Bewust hier en niet op import-time: tests monkeypatchen `settings`
    *vóór* ze de app instantiëren via `TestClient`/`ASGITransport`, en
    zouden anders de productie-paden raken. `CREATE TABLE IF NOT EXISTS`
    maakt dubbele init's bovendien gratis.
    """
    init_databases()
    yield


app = FastAPI(title=f"{pylades_display()} proxy", version=__version__, lifespan=_lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Request-body
# ---------------------------------------------------------------------------


class MessagesRequest(BaseModel):
    """Body-contract voor `POST /v1/messages` (zie PLAN §15a).

    `template_id` en `dossier` zijn verplicht; `resume_session` is alleen
    gezet bij een hervat na review. `model`, `provider` en `max_tokens`
    leven op de template — niet hier — om te voorkomen dat clients de
    aan het LLM gestuurde opdracht ad-hoc kunnen overrulen.
    """

    template_id: int = Field(gt=0)
    dossier: str = Field(min_length=1)
    resume_session: str | None = Field(default=None, min_length=1)


# ---------------------------------------------------------------------------
# Pijplijn (per request) — pure functies waar mogelijk
# ---------------------------------------------------------------------------


def _merge_resumed_decisions(
    detection: DetectionResult,
    accepted: list[Entity],
    rejected_originals: set[tuple[str, str]],
) -> DetectionResult:
    """Pas de reviewer-beslissingen toe op een nieuwe detectie-uitslag.

    - ACCEPTED/MODIFIED entiteiten vullen `confident_entities` aan (op basis
      van match op `original` + `entity_type` met re-detected items).
    - REJECTED items verdwijnen uit elke bucket (de reviewer zei "geen
      entity"); pending-items die nog wél als (origineel, type) matchen
      vallen dus weg.

    Detectie-spans blijven leidend; de queue levert alleen ja/nee/typewissel.
    """
    accepted_by_text: dict[str, Entity] = {e.original: e for e in accepted}

    new_confident: list[Entity] = []
    for ent in detection.confident_entities:
        if (ent.original, ent.entity_type.value) in rejected_originals:
            continue
        if ent.original in accepted_by_text:
            decided = accepted_by_text[ent.original]
            new_confident.append(
                ent.model_copy(update={"entity_type": decided.entity_type, "category": None})
            )
            continue
        new_confident.append(ent)

    new_pending: list[Entity] = []
    for ent in detection.pending_review:
        if (ent.original, ent.entity_type.value) in rejected_originals:
            continue
        if ent.original in accepted_by_text:
            decided = accepted_by_text[ent.original]
            new_confident.append(
                ent.model_copy(update={"entity_type": decided.entity_type, "category": None})
            )
            continue
        new_pending.append(ent)

    return DetectionResult(confident_entities=new_confident, pending_review=new_pending)


def _rejected_originals_for_session(session_id: str) -> set[tuple[str, str]]:
    """Lever `(detected_text, proposed_entity_type)` van alle REJECTED items.

    Cross-tabel-join blijft binnen `proxy/review` namespace via
    `get_accepted_entities` voor het ACCEPTED/MODIFIED-pad; hier doen we
    één extra query inline omdat de proxy de enige consument is.
    """
    with get_content_connection() as conn:
        rows = conn.execute(
            """SELECT detected_text, proposed_entity_type
                 FROM review_queue
                WHERE session_id = ? AND status = ?""",
            (session_id, ReviewStatus.REJECTED.value),
        ).fetchall()
    return {(str(r["detected_text"]), str(r["proposed_entity_type"])) for r in rows}


def _depseudonymize_response_body(body: Any, session_id: str) -> Any:
    """Vervang TWO_WAY-pseudoniemen in `content[i].text` van de response.

    Werkt diep maar conservatief: enkel `text`-strings binnen blokken; geen
    metadata, geen `id`, geen `usage`. Mutates a deep-copy.
    """
    if not isinstance(body, dict):
        return body
    content = body.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] = depseudonymize(block["text"], session_id)
    return body


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@app.post("/v1/messages")
async def messages(payload: MessagesRequest) -> JSONResponse:
    template = _load_template_or_raise(payload.template_id)
    if template.llm_provider.lower() != "anthropic":
        raise HTTPException(
            status_code=501,
            detail=(
                "Provider "
                f"{template.llm_provider!r} is niet ondersteund in {target_version_display()}; "
                "alleen 'anthropic'. Zie roadmap v1.0."
            ),
        )
    assembled = _assemble_prompt_or_raise(template, payload.dossier)

    session_id = payload.resume_session or uuid.uuid4().hex
    is_resume = payload.resume_session is not None

    detection = detect_all(assembled, use_llm=template.use_llm)
    if is_resume:
        accepted = get_accepted_entities(session_id)
        rejected = _rejected_originals_for_session(session_id)
        detection = _merge_resumed_decisions(detection, accepted, rejected)

    if detection.pending_review:
        review_ids = enqueue(session_id, assembled, detection.pending_review)
        return JSONResponse(
            status_code=423,
            content={
                "session_id": session_id,
                "review_required": True,
                "review_item_ids": review_ids,
                "review_url": f"/ui/review?session={session_id}",
                "message": (
                    "Detectie-confidence ligt onder de threshold; los de review-queue "
                    "op en herstuur met body-veld `resume_session`."
                ),
            },
        )

    if is_resume and not all_resolved(session_id):
        # Defensieve dubbele check: de resume-flag kwam binnen, maar er staan nog
        # PENDING-items uit een eerdere call. Niet doorgaan.
        return JSONResponse(
            status_code=423,
            content={
                "session_id": session_id,
                "review_required": True,
                "message": "Er staan nog PENDING-items in de queue voor deze sessie.",
            },
        )

    pseudonym_manager = PseudonymManager.from_session(session_id)
    gen_text, gen_entities = generalize_all(assembled, detection.confident_entities)
    pseudo_text, persisted_entities = pseudonymize(
        gen_text,
        gen_entities,
        session_id,
        template,
        manager=pseudonym_manager,
    )

    confidences = [ent.confidence for ent in persisted_entities if ent.confidence is not None]
    avg_conf = mean(confidences) if confidences else None

    upstream_request_body: dict[str, Any] = {
        "model": template.llm_naam,
        "max_tokens": template.max_tokens,
        "messages": [{"role": "user", "content": pseudo_text}],
    }
    upstream_status, upstream_body, upstream_text = await _call_upstream(upstream_request_body)

    if upstream_status >= 400:
        log_request(
            session_id=session_id,
            template_id=template.id,
            original_prompt=assembled,
            pseudonymized_prompt=pseudo_text,
            response_pseudonymized=upstream_text,
            response_depseudonymized=None,
            llm_provider=template.llm_provider,
            llm_model=template.llm_naam,
            avg_confidence=avg_conf,
            review_required=False,
            error=f"upstream HTTP {upstream_status}",
        )
        return JSONResponse(status_code=upstream_status, content=upstream_body)

    depseudonymized_body = _depseudonymize_response_body(upstream_body, session_id)

    log_request(
        session_id=session_id,
        template_id=template.id,
        original_prompt=assembled,
        pseudonymized_prompt=pseudo_text,
        response_pseudonymized=upstream_text,
        response_depseudonymized=_safe_dumps(depseudonymized_body),
        llm_provider=template.llm_provider,
        llm_model=template.llm_naam,
        avg_confidence=avg_conf,
        review_required=False,
        error=None,
    )
    return JSONResponse(
        status_code=upstream_status,
        content=depseudonymized_body,
        headers={"X-Pylades-Session": session_id},
    )


# ---------------------------------------------------------------------------
# Sub-orkestratie (klein gehouden voor mocking)
# ---------------------------------------------------------------------------


def _load_template_or_raise(template_id: int) -> Template:
    template = get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} bestaat niet")
    return template


def _assemble_prompt_or_raise(template: Template, dossier: str) -> str:
    """Bouw de samengestelde prompt; faal hard bij een ongeldig template.

    De Pydantic-validator op `Template.prompt_tekst` (zie `shared/models.py`)
    hoort hier alle invalide gevallen al af te vangen vóórdat de template
    in de DB belandt; een 500 hier betekent dus dat er een rij is die
    buiten de CRUD om is geschreven. Beter gillen dan een lege of
    half-samengestelde prompt naar Anthropic sturen.
    """
    if not template.prompt_tekst:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Template {template.id} heeft een lege prompt_tekst; "
                "open hem in de Prompts-pagina en voeg een opdracht + {input} toe."
            ),
        )
    if "{input}" not in template.prompt_tekst:
        raise HTTPException(
            status_code=500,
            detail=(f"Template {template.id} mist verplichte `{{input}}`-placeholder."),
        )
    return template.prompt_tekst.replace("{input}", dossier)


async def _call_upstream(body: dict[str, Any]) -> tuple[int, Any, str]:
    """Stuur het pseudonimized request naar Anthropic en lever (status, json, text)."""
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    async with _create_upstream_client() as client:
        try:
            response = await client.post(_ANTHROPIC_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Upstream Anthropic-call faalde: %s", exc)
            return 502, {"error": "upstream_unreachable", "detail": str(exc)}, ""
    text = response.text
    try:
        return response.status_code, response.json(), text
    except ValueError:
        return response.status_code, {"raw": text}, text


def _safe_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)
