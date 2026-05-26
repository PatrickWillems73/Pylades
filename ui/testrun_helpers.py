"""Pure helpers voor de testrun-flow op de homepagina (Eenvoudig + Uitgebreid).

Splitten we doelbewust van `ui/Home.py` af zodat we de logica met `pytest`
kunnen valideren zonder Streamlit-context. De Streamlit-pagina blijft een
dunne presentatielaag bovenop deze helpers.

Sinds doelversie v0.3 (PLAN §15a) heeft een prompt-template exact één
placeholder `{input}`; deze module bevat de substitutie-helper, de dry-run-
orkestrator en de plain-language-vertalingen die de UI gebruikt voor de
samenvattingskaart, entiteit-kaartjes, curl-equivalent en privacy-rapport-
export.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from proxy.audit import get_logs_by_session
from proxy.detection import detect_all
from proxy.generalization import generalize_all
from proxy.mapping import list_entities_for_session
from proxy.pseudonymization import (
    get_super_default_pseudonymization_mode,
    pseudonymize_dry_run,
    resolve_effective_mode,
)
from proxy.review import ReviewItem, get_accepted_entities, get_pending
from shared.config import settings
from shared.crypto import derive_session_key, load_or_create_secret, make_pseudonym
from shared.models import (
    AuditEntry,
    Entity,
    PseudonymizationMode,
    Template,
)


def fill_input(prompt_tekst: str, dossier: str) -> str:
    """Vervang `{input}` in een prompt-template door de dossier-tekst.

    Tolerante substitutie: bij een ongevalideerd template (geen `{input}`)
    plakken we het dossier eronder, en bij een lege template-tekst leveren
    we het dossier zelf terug. De Pydantic-validator op `Template.prompt_tekst`
    zou dit normaliter al bij opslag voorkomen; deze tolerantie is voor het
    preview-pad (Streamlit) zodat de UI nooit crasht op een edge-case.
    """
    if "{input}" in prompt_tekst:
        return prompt_tekst.replace("{input}", dossier)
    if not prompt_tekst:
        return dossier
    return f"{prompt_tekst}\n\n{dossier}"


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Uitkomst van `analyze_prompt`: alles wat de UI nodig heeft voor de preview."""

    session_id: str
    original: str
    generalized: str
    pseudonymized: str
    entities: list[Entity]
    pending_review: list[Entity]


def analyze_prompt(template: Template, dossier: str) -> AnalysisResult:
    """Detect → generalize → dry-run pseudonimiseren, zonder vault-writes.

    Stelt de prompt eerst samen via `fill_input`. Genereert een verse
    `session_id` per analyse zodat het preview-resultaat losstaat van
    eerdere sessies en geen rest-state van vorige clicks toont. De
    daadwerkelijke verstuur-flow gebruikt een eigen session_id en doet
    de echte (persisterende) pijplijn via de proxy.
    """
    assembled = fill_input(template.prompt_tekst, dossier)
    session_id = uuid.uuid4().hex
    detection = detect_all(assembled, use_llm=template.use_llm)
    gen_text, gen_entities = generalize_all(assembled, detection.confident_entities)
    pseudo_text, annotated = pseudonymize_dry_run(
        gen_text,
        gen_entities,
        session_id,
        template,
    )
    return AnalysisResult(
        session_id=session_id,
        original=assembled,
        generalized=gen_text,
        pseudonymized=pseudo_text,
        entities=annotated,
        pending_review=list(detection.pending_review),
    )


def _entity_from_review_item(item: ReviewItem) -> Entity:
    return Entity(
        original=item.detected_text,
        entity_type=item.proposed_entity_type,
        category=item.proposed_category,
        confidence=item.confidence,
        detection_layer=item.detection_layer,
        start=0,
        end=len(item.detected_text),
    )


def _position_entities_in_text(text: str, entities: list[Entity]) -> list[Entity]:
    """Zoek voorkomens in `text` zodat pseudonimisering geldige spans heeft."""
    positioned: list[Entity] = []
    search_from = 0
    for ent in entities:
        idx = text.find(ent.original, search_from)
        if idx < 0:
            idx = text.find(ent.original)
        if idx >= 0:
            end = idx + len(ent.original)
            positioned.append(ent.model_copy(update={"start": idx, "end": end}))
            search_from = max(search_from, end)
        else:
            positioned.append(ent)
    return positioned


def _annotate_entities_for_display(
    text: str,
    entities: list[Entity],
    session_id: str,
    template: Template,
) -> tuple[str, list[Entity]]:
    """Pseudoniemen + preview-tekst voor de UI (zonder vault-writes)."""
    if not entities:
        return text, []
    positioned = _position_entities_in_text(text, entities)
    try:
        return pseudonymize_dry_run(text, positioned, session_id, template)
    except ValueError:
        super_default = get_super_default_pseudonymization_mode()
        secret = load_or_create_secret(settings.global_secret_path)
        session_key = derive_session_key(secret, session_id)
        annotated: list[Entity] = []
        for ent in entities:
            mode = resolve_effective_mode(template, ent.entity_type, super_default)
            pseudo = make_pseudonym(session_key, ent.original, ent.entity_type)
            annotated.append(
                ent.model_copy(update={"pseudonym": pseudo, "effective_mode": mode}),
            )
        return text, annotated


def reconcile_analysis_for_display(
    result: AnalysisResult,
    template: Template,
    *,
    proxy_session_id: str | None,
    session_resolved: bool,
    response_status: int | None,
) -> AnalysisResult:
    """Sync preview-lijsten met review-queue en vault na echte proxy-calls.

    De dry-run (`analyze_prompt`) blijft in session state staan; zodra er een
    proxy-sessie is, is de DB de bron van waarheid voor pending/accepted en
    (na 200) voor pseudoniemen in **Beschermde gegevens**.
    """
    if not proxy_session_id:
        return result

    entities = list(result.entities)
    pending: list[Entity] = []
    pseudonymized = result.pseudonymized

    if response_status == 200:
        vault_entities = list_entities_for_session(proxy_session_id)
        if vault_entities:
            entities = vault_entities
        logs = get_logs_by_session(proxy_session_id)
        if logs:
            pseudonymized = logs[-1].pseudonymized_prompt
        return AnalysisResult(
            session_id=proxy_session_id,
            original=result.original,
            generalized=result.generalized,
            pseudonymized=pseudonymized,
            entities=entities,
            pending_review=[],
        )

    if session_resolved:
        known = {ent.original for ent in entities}
        for ent in get_accepted_entities(proxy_session_id):
            if ent.original not in known:
                entities.append(ent)
                known.add(ent.original)
    else:
        pending = [_entity_from_review_item(item) for item in get_pending(proxy_session_id)]

    if entities:
        pseudonymized, entities = _annotate_entities_for_display(
            result.generalized,
            entities,
            proxy_session_id,
            template,
        )

    return AnalysisResult(
        session_id=proxy_session_id,
        original=result.original,
        generalized=result.generalized,
        pseudonymized=pseudonymized,
        entities=entities,
        pending_review=pending,
    )


# ---------------------------------------------------------------------------
# Plain-language helpers voor de Eenvoudig-modus
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LaySummary:
    """Samenvatting van een testrun in gebruikers-taal (Eenvoudig).

    Bewust gescheiden velden i.p.v. één voorgeformatteerde zin: de UI
    kiest zelf hoe ze de cijfers en zinnen toont (status-strip, kaart,
    badges). Tests controleren de cijfers, de UI mag de presentatie
    vrij wijzigen.
    """

    protected_count: int
    two_way_count: int
    pending_count: int
    summary_line: str


def summarize_for_lay_user(
    result: AnalysisResult,
    *,
    response_status: int | None = None,
    session_resolved: bool = False,
) -> LaySummary:
    """Bouw een leesbare samenvatting van een testrun.

    `response_status` mag `None` zijn (alleen dry-run gedaan); is hij
    gezet, dan voegen we een korte status-zin toe (200 = antwoord
    ontvangen, 423 = wacht op beslissingen, ander = fout).

    `session_resolved=True` corrigeert een achtergebleven HTTP 423 na
    afhandeling van de review-queue voor deze sessie.
    """
    protected = len(result.entities)
    two_way = sum(
        1 for ent in result.entities if ent.effective_mode is PseudonymizationMode.TWO_WAY
    )
    pending = len(result.pending_review)

    parts: list[str] = []
    parts.append(_plural(protected, "gegeven beschermd", "gegevens beschermd"))
    if two_way:
        parts.append(_plural(two_way, "naam vertaald terug", "namen vertaald terug"))
    if pending:
        parts.append(_plural(pending, "wacht op beoordeling", "wachten op beoordeling"))
    if response_status is not None:
        parts.append(_status_phrase(response_status, session_resolved=session_resolved))

    return LaySummary(
        protected_count=protected,
        two_way_count=two_way,
        pending_count=pending,
        summary_line=" · ".join(parts),
    )


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _status_phrase(status: int, *, session_resolved: bool = False) -> str:
    if status == 423:
        return "beslissingen afgehandeld" if session_resolved else "wacht op beslissingen"
    if status == 200:
        return "antwoord ontvangen"
    if status == 0:
        return "geen verbinding met de proxy"
    if status >= 500:
        return f"fout bij het externe LLM (HTTP {status})"
    if status >= 400:
        return f"fout (HTTP {status})"
    return f"HTTP {status}"


def lay_explanation(entity: Entity, *, pending: bool = False) -> str:
    """Eén-regel-uitleg van wat er met deze entiteit gebeurt, in gewone taal."""
    if pending:
        return (
            "Wacht op jouw beoordeling — Pylades is niet zeker of dit "
            "persoonsgegevens zijn."
        )
    if entity.effective_mode is PseudonymizationMode.TWO_WAY:
        return (
            "Verborgen voor het externe LLM en in het antwoord teruggezet "
            "naar het origineel."
        )
    return "Verborgen — het externe LLM ziet alleen een code, geen origineel."


def entity_type_label(entity: Entity) -> str:
    """Mens-vriendelijk label voor een EntityType.

    Bewust een dunne mapping i.p.v. een i18n-laag: de codebase is NL-only.
    Onbekende types vallen terug op de StrEnum-waarde.
    """
    return _TYPE_LABELS.get(entity.entity_type.value, entity.entity_type.value)


_TYPE_LABELS: dict[str, str] = {
    "bsn": "BSN",
    "name": "Naam",
    "email": "E-mailadres",
    "phone": "Telefoonnummer",
    "iban": "IBAN",
    "postcode_pc6": "Postcode (PC6)",
    "postcode_pc2": "Postcode (PC2)",
    "address": "Adres",
    "birthdate": "Geboortedatum",
    "birth_year": "Geboortejaar",
    "age": "Leeftijd",
    "mrn": "Medisch dossiernummer",
    "epd_id": "EPD-identifier",
    "kenteken": "Kenteken",
    "icd10_code": "ICD-10-code",
    "diagnosis": "Diagnose",
    "admission_date": "Opnamedatum",
    "discharge_date": "Ontslagdatum",
    "exam_date": "Onderzoeksdatum",
    "org": "Organisatie",
    "location": "Locatie",
    "product": "Productnaam",
    "project": "Projectnaam",
}


# ---------------------------------------------------------------------------
# Response-inspectie: wat blijft in beeld, wat onder de expander
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResponseSignals:
    """Signalen die in Eenvoudig prominent moeten blijven (niet onder expander).

    De Anthropic-JSON verdwijnt achter een expander in Eenvoudig, maar
    deze velden moeten zichtbaar in beeld blijven omdat ze het verschil
    maken tussen "geslaagd" en "stilletjes mislukt".
    """

    ok: bool
    status: int
    assistant_text: str
    stop_reason: str | None
    error_message: str | None
    is_truncated: bool
    is_refusal: bool
    is_empty: bool


def response_signals(status: int, payload: Any) -> ResponseSignals:
    """Vat de belangrijke signalen uit een proxy-response samen.

    `payload` is de gedeserialiseerde JSON (dict) of een fallback-object
    (`{"raw": "..."}`); we proberen er een assistant-tekst en stop_reason
    uit te halen, en classificeren de uitkomst.
    """
    assistant_text = extract_assistant_text(payload)
    stop_reason: str | None = None
    error_message: str | None = None

    if isinstance(payload, dict):
        raw_stop = payload.get("stop_reason")
        if isinstance(raw_stop, str):
            stop_reason = raw_stop
        raw_error = payload.get("error")
        if isinstance(raw_error, str):
            error_message = raw_error
        elif isinstance(raw_error, dict):
            err_msg = raw_error.get("message") or raw_error.get("detail")
            if isinstance(err_msg, str):
                error_message = err_msg
        detail = payload.get("detail")
        if error_message is None and isinstance(detail, str):
            error_message = detail

    is_truncated = stop_reason == "max_tokens"
    is_refusal = stop_reason == "refusal"
    is_empty = status == 200 and not assistant_text.strip()
    ok = status == 200 and not is_refusal and not is_empty

    return ResponseSignals(
        ok=ok,
        status=status,
        assistant_text=assistant_text,
        stop_reason=stop_reason,
        error_message=error_message,
        is_truncated=is_truncated,
        is_refusal=is_refusal,
        is_empty=is_empty,
    )


def extract_assistant_text(payload: Any) -> str:
    """Trek de assistant-tekst uit een Anthropic Messages-response.

    Structuur: `{"content": [{"type": "text", "text": "..."}, ...], ...}`.
    Bij afwijkende payloads (fout-shapes, `{"raw": "..."}`) leveren we
    een lege string i.p.v. een exception; de caller toont dan de
    technische details onder de expander.
    """
    if not isinstance(payload, dict):
        return ""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    out: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            out.append(text)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Curl-equivalent (Uitgebreid)
# ---------------------------------------------------------------------------


def format_curl_equivalent(
    *,
    template_id: int,
    dossier: str,
    proxy_port: int,
    resume_session: str | None = None,
) -> str:
    """Bouw een copy-pasteable curl-commando dat dezelfde request stuurt.

    Conform PLAN §15a body-contract: `template_id`, `dossier`,
    optioneel `resume_session`. We escapen single-quotes in het dossier
    door het hele blok in een heredoc te zetten, zodat namen en zinnen
    met apostrofs intact blijven.
    """
    body: dict[str, Any] = {
        "template_id": template_id,
        "dossier": dossier,
    }
    if resume_session:
        body["resume_session"] = resume_session
    payload_lines = _format_json_pretty(body).splitlines()
    indented = "\n".join("    " + line for line in payload_lines)
    return (
        f"curl -X POST http://127.0.0.1:{proxy_port}/v1/messages \\\n"
        '  -H "Content-Type: application/json" \\\n'
        "  -d @- <<'JSON'\n"
        f"{indented}\n"
        "JSON"
    )


def _format_json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Privacy-rapport export (markdown + CSV)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrivacyReportContext:
    """Bundel van inputs voor een privacy-rapport-export.

    Eén dataclass i.p.v. losse argumenten: scope-uitbreiding (laatste run
    vs hele sessie) en eventuele extra audit-rijen zijn nu één toevoegd
    veld groot. De UI-laag stelt deze context samen, de format-helpers
    blijven puur.
    """

    template_naam: str
    template_groep: str
    session_id: str
    response_status: int | None
    response_stop_reason: str | None
    audit_entries: tuple[AuditEntry, ...] = ()
    two_way_justification: str | None = None


def build_privacy_report_md(
    result: AnalysisResult,
    context: PrivacyReportContext,
) -> str:
    """Schrijf een mens-leesbaar privacy-rapport in markdown.

    Bevat de samenvatting, de entiteit-tabel, eventuele wacht-op-review
    items, de TWO_WAY-onderbouwing van de template, en (bij sessie-scope)
    een tijdslijn van audit-rijen.
    """
    summary = summarize_for_lay_user(result, response_status=context.response_status)
    lines: list[str] = []
    lines.append("# Pylades — privacy-rapport")
    lines.append("")
    lines.append(
        f"- **Prompt:** {context.template_groep} / {context.template_naam}"
    )
    lines.append(f"- **Sessie-id:** `{context.session_id}`")
    if context.response_status is not None:
        lines.append(f"- **Status laatste call:** HTTP {context.response_status}")
    if context.response_stop_reason:
        lines.append(f"- **Stop-reden externe LLM:** `{context.response_stop_reason}`")
    lines.append(f"- **Samenvatting:** {summary.summary_line}")
    lines.append("")

    lines.append("## Beschermde gegevens")
    if result.entities:
        lines.append("")
        lines.append(
            "| Origineel | Type | Behandeling | Vertaald terug |"
        )
        lines.append("| --- | --- | --- | --- |")
        for ent in result.entities:
            two_way = ent.effective_mode is PseudonymizationMode.TWO_WAY
            lines.append(
                f"| {ent.original} | {entity_type_label(ent)} | "
                f"{_md_treatment(ent)} | {'ja' if two_way else 'nee'} |"
            )
    else:
        lines.append("")
        lines.append("_Geen entiteiten gedetecteerd._")
    lines.append("")

    if result.pending_review:
        lines.append("## Wacht op jouw beoordeling")
        lines.append("")
        for ent in result.pending_review:
            lines.append(
                f"- **{ent.original}** — voorgesteld type: "
                f"{entity_type_label(ent)} "
                f"(confidence {ent.confidence:.2f}, laag `{ent.detection_layer.value}`)"
            )
        lines.append("")

    if context.two_way_justification:
        lines.append("## TWO_WAY-onderbouwing (BR-C06)")
        lines.append("")
        lines.append(f"> {context.two_way_justification}")
        lines.append("")

    if context.audit_entries:
        lines.append("## Audit-tijdslijn (sessie-scope)")
        lines.append("")
        lines.append("| Audit-id | Tijd | Status | LLM-model | Review nodig |")
        lines.append("| --- | --- | --- | --- | --- |")
        for entry in context.audit_entries:
            ts = entry.created_at.isoformat() if entry.created_at else ""
            status = entry.error or "ok"
            review = "ja" if entry.review_required else "nee"
            lines.append(
                f"| {entry.id} | {ts} | {status} | "
                f"{entry.llm_model or ''} | {review} |"
            )
        lines.append("")

    return "\n".join(lines)


def _md_treatment(ent: Entity) -> str:
    if ent.effective_mode is PseudonymizationMode.TWO_WAY:
        return "verborgen, vertaald terug"
    return "verborgen voor extern LLM"


def build_privacy_report_csv(
    result: AnalysisResult,
    context: PrivacyReportContext,
) -> str:
    """Schrijf een entiteit-CSV voor analyse over runs.

    Vast schema (kolomvolgorde stabiel): session_id, original, type,
    category, treatment, two_way, pseudonym, confidence, detection_layer.
    Bij sessie-scope is `session_id` overal hetzelfde, bij run-scope
    eveneens — de kolom is bewust altijd aanwezig zodat downstream-tools
    niet hoeven te raden.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "session_id",
            "original",
            "type",
            "category",
            "treatment",
            "two_way",
            "pseudonym",
            "confidence",
            "detection_layer",
            "pending_review",
        ]
    )
    for ent in result.entities:
        writer.writerow(
            [
                context.session_id,
                ent.original,
                ent.entity_type.value,
                ent.category.value if ent.category else "",
                _md_treatment(ent),
                "true" if ent.effective_mode is PseudonymizationMode.TWO_WAY else "false",
                ent.pseudonym or "",
                f"{ent.confidence:.2f}",
                ent.detection_layer.value,
                "false",
            ]
        )
    for ent in result.pending_review:
        writer.writerow(
            [
                context.session_id,
                ent.original,
                ent.entity_type.value,
                ent.category.value if ent.category else "",
                "wacht op beoordeling",
                "false",
                "",
                f"{ent.confidence:.2f}",
                ent.detection_layer.value,
                "true",
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Hulp voor highlight-rendering (gedeeld door Eenvoudig + Uitgebreid)
# ---------------------------------------------------------------------------


def highlight_pairs(
    result: AnalysisResult,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """Lever drie `(needle, css_key)`-paren-lijsten voor preview-highlights.

    Volgorde: originals (incl. pending), generalized, pseudonyms. Past
    `Entity.original`/`generalized_to`/`pseudonym` op aan de css-key die
    de UI hangt aan ONE_WAY / TWO_WAY / pending. Gescheiden van de
    Streamlit-render zodat een unit-test kan controleren dat een
    pending-entity correct als `"pending"` gemarkeerd wordt.
    """
    originals: list[tuple[str, str]] = []
    for ent in result.entities:
        originals.append((ent.original, _css_key(ent, pending=False)))
    for ent in result.pending_review:
        originals.append((ent.original, _css_key(ent, pending=True)))

    generalized: list[tuple[str, str]] = [
        ((ent.generalized_to or ent.original), _css_key(ent, pending=False))
        for ent in result.entities
    ]
    pseudonyms: list[tuple[str, str]] = [
        ((ent.pseudonym or ""), _css_key(ent, pending=False))
        for ent in result.entities
        if ent.pseudonym
    ]
    return originals, generalized, pseudonyms


def pseudonymized_highlights(result: AnalysisResult) -> list[tuple[str, str]]:
    """Highlights voor de pseudonimized preview: placeholders + pending originals."""
    _, _, pseudo_h = highlight_pairs(result)
    pending_h = [
        (ent.original, "pending")
        for ent in result.pending_review
        if ent.original
    ]
    return [*pseudo_h, *pending_h]


def _css_key(entity: Entity, *, pending: bool) -> str:
    if pending:
        return "pending"
    if entity.effective_mode is PseudonymizationMode.TWO_WAY:
        return "two_way"
    return "one_way"


def collect_highlight_inputs(
    entities: Iterable[Entity], *, pending: bool = False
) -> list[tuple[str, str]]:
    """Gemakshelper voor één lijst entiteiten naar `(needle, css_key)`-paren.

    Gebruikt door de Uitgebreid-render die per kolom alleen één bron
    nodig heeft (originals óf generalized óf pseudonyms).
    """
    return [(ent.original, _css_key(ent, pending=pending)) for ent in entities]
