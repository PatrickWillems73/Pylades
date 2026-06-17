"""Schrijf een evaluatie-rapport weg als JSON, CSV en een compacte HTML.

Stdlib-only (json/csv): geen extra dependencies voor de basisrapportage. De
bestandsnamen krijgen een timestamp zodat opeenvolgende runs naast elkaar
bewaard blijven voor vergelijking.
"""

from __future__ import annotations

import csv
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.evaluate import all_entity_types
from eval.metrics.generalization import format_generalization_summary
from shared.models import ENTITY_CATEGORY_MAP, EntityCategory, EntityType


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _nl_num(value: Any) -> str:
    """Nederlandse getalopmaak: komma als decimaalteken, punt voor duizendtallen.

    Locale-onafhankelijk (geen `locale`-afhankelijkheid die per OS verschilt):
    we formatteren met de Engelse scheidingstekens en wisselen ze daarna om.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    if isinstance(value, float):
        return f"{value:,}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return str(value)


# Korte privacy-klasse-labels achter indirect-identifier-rijen.
_CATEGORY_SHORT = {
    EntityCategory.DIRECT_IDENTIFIER: "direct",
    EntityCategory.QUASI_IDENTIFIER: "quasi",
    EntityCategory.CLINICAL_SENSITIVE: "clinical",
    EntityCategory.FREE_TEXT: "free text",
}


def _category_short(etype: str) -> str:
    """Kort label voor de privacy-klasse van een type (bv. 'quasi')."""
    try:
        category = ENTITY_CATEGORY_MAP[EntityType(etype)]
    except (KeyError, ValueError):
        return ""
    return _CATEGORY_SHORT.get(category, "")


def _warmup_note(latency: dict[str, Any]) -> str:
    if latency.get("warmup"):
        warmup_ms = _nl_num(latency.get("warmup_ms"))
        return (
            f"Warm-up toegepast: eerste cold-start-aanroep ({warmup_ms} ms) "
            "telt niet mee in de percentielen hierboven."
        )
    return "Geen warm-up: de cold-start is meegenomen in de percentielen hierboven."


def write_json(report: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def write_csv(report: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    exact = report["scores"]["exact"]["by_type"]
    overlap = report["scores"]["overlap"]["by_type"]
    predicted = report.get("predicted_by_type", {})
    types = sorted(set(exact) | set(overlap) | set(all_entity_types()))
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "entity_type", "group", "p",
                "exact_tp", "exact_fp", "exact_fn", "exact_precision", "exact_recall", "exact_f1",
                "overlap_tp", "overlap_fp", "overlap_fn",
                "overlap_precision", "overlap_recall", "overlap_f1",
            ]
        )
        for etype in types:
            e = exact.get(etype, {})
            o = overlap.get(etype, {})
            writer.writerow(
                [
                    etype, _group_of(etype), predicted.get(etype, 0),
                    e.get("tp", 0), e.get("fp", 0), e.get("fn", 0),
                    e.get("precision", 0), e.get("recall", 0), e.get("f1", 0),
                    o.get("tp", 0), o.get("fp", 0), o.get("fn", 0),
                    o.get("precision", 0), o.get("recall", 0), o.get("f1", 0),
                ]
            )
    return out


def _spacy_label(spacy_model: str | None) -> str:
    """`nl_core_news_md` → 'spacy md'; valt terug op 'spacy' zonder modelinfo."""
    if not spacy_model:
        return "spacy"
    return f"spacy {spacy_model.split('_')[-1]}"


def _layer_label(layer: str, run_meta: dict[str, Any]) -> str:
    """Specifieke modelnaam per laag, bv. 'spacy md' of 'llm qwen3:1.7b'."""
    if layer == "spacy":
        return _spacy_label(run_meta.get("spacy_model"))
    if layer == "llm":
        model = run_meta.get("llm_model")
        return f"llm {model}" if model else "llm"
    return layer  # regex (geen modelvariant)


def _format_observed(layers: dict[str, int], run_meta: dict[str, Any]) -> str:
    """Waargenomen lagen met modelnaam, bv. 'spacy md, regex' (aantal → kolom p)."""
    return ", ".join(_layer_label(layer, run_meta) for layer in layers)


def _format_expected(
    etype: str, run_meta: dict[str, Any], detector_layers: dict[str, list[str]]
) -> str:
    """Welk model een niet-voorspeld type zou detecteren; meldt laag 3 indien uit."""
    expected = detector_layers.get(etype, [])
    if not expected:
        return "geen detector"
    parts: list[str] = []
    for layer in expected:
        label = _layer_label(layer, run_meta)
        if layer == "llm" and not run_meta.get("use_llm"):
            parts.append(f"{label} (laag 3 niet gedraaid)")
        else:
            parts.append(f"{label} (verwacht)")
    return "; ".join(parts)


_EMPTY_SCORE = {"tp": 0, "fp": 0, "fn": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0}

# Twee tabelgroepen: direct-identifier vs. al het overige (indirect-identifier).
# Bewuste keuze om elke rij te behouden — ook clinical_sensitive (diagnose/ICD)
# en free_text (product/project, de laag-3-output) vallen onder "indirect".
_GROUP_DIRECT = "direct"
_GROUP_INDIRECT = "indirect"
_GROUP_ORDER = (_GROUP_DIRECT, _GROUP_INDIRECT)
_GROUP_LABELS = {_GROUP_DIRECT: "Direct-identifier", _GROUP_INDIRECT: "Indirect-identifier"}


def _group_of(etype: str) -> str:
    """Direct-identifier of indirect-identifier (al het niet-directe)."""
    try:
        category = ENTITY_CATEGORY_MAP[EntityType(etype)]
    except (KeyError, ValueError):
        return _GROUP_INDIRECT
    return _GROUP_DIRECT if category == EntityCategory.DIRECT_IDENTIFIER else _GROUP_INDIRECT


def _performance_section(report: dict[str, Any]) -> str:
    """Korte performance-samenvatting onder de run-header."""
    perf = report.get("performance", {})
    latency = report.get("latency", {})
    direct_macro_f1 = perf.get("direct_identifier_macro_f1", 0)
    indirect_macro_f1 = perf.get("indirect_identifier_macro_f1", 0)
    mean_ms = perf.get("latency_mean_ms", latency.get("mean_ms", 0))
    gen_summary = format_generalization_summary(report.get("generalization"))
    return (
        "<h2>Performance van de testrun</h2>"
        f"<p>Direct-identifier macro-F1: {_nl_num(direct_macro_f1)}<br>"
        f"Indirect-identifier macro-F1: {_nl_num(indirect_macro_f1)}<br>"
        f"Generalisatie BR-B: {gen_summary}<br>"
        f"Latency mean: {_nl_num(mean_ms)}ms</p>"
    )


def format_run_summary(report: dict[str, Any]) -> str:
    """Terminal-samenvatting: run-header + performance-blok."""
    totals = report["totals"]
    perf = report.get("performance", {})
    header = (
        f"Runner: {report['runner']} · records: {_nl_num(totals['records'])} · "
        f"gold-entities: {_nl_num(totals['gold_entities'])} · "
        f"direct-identifiers: {_nl_num(totals['direct_identifiers'])}"
    )
    direct_macro_f1 = perf.get("direct_identifier_macro_f1", 0)
    indirect_macro_f1 = perf.get("indirect_identifier_macro_f1", 0)
    mean_ms = perf.get("latency_mean_ms", report.get("latency", {}).get("mean_ms", 0))
    gen_summary = format_generalization_summary(report.get("generalization"))
    performance = (
        "Performance van de testrun\n"
        f"Direct-identifier macro-F1: {_nl_num(direct_macro_f1)}\n"
        f"Indirect-identifier macro-F1: {_nl_num(indirect_macro_f1)}\n"
        f"Generalisatie BR-B: {gen_summary}\n"
        f"Latency mean: {_nl_num(mean_ms)}ms"
    )
    return f"{header}\n\n{performance}"


def _context_section(report: dict[str, Any]) -> str:
    """Beschrijf de testrun-context: hardware en model/techniek per laag."""
    env = report.get("environment", {})
    layers = report.get("layers_config", {})
    rows = "".join(
        f"<tr><td>{label}</td><td>{value}</td></tr>"
        for label, value in (
            ("Hardware", env.get("summary", "onbekend")),
            ("Besturingssysteem", env.get("os", "onbekend")),
            ("Python", env.get("python", "onbekend")),
            ("Identificatie-laag 1", layers.get("layer1", "regex")),
            ("Identificatie-laag 2", layers.get("layer2", "onbekend")),
            ("Identificatie-laag 3", layers.get("layer3", "onbekend")),
        )
    )
    return f"<h2>Context van de testrun</h2><table>{rows}</table>"


def _score_cells(p: int, s: dict[str, Any]) -> str:
    """De cijferkolommen p, tp, fp, fn, P, R, F1 voor één rij (NL-opmaak)."""
    return (
        f"<td>{_nl_num(p)}</td><td>{_nl_num(s['tp'])}</td><td>{_nl_num(s['fp'])}</td>"
        f"<td>{_nl_num(s['fn'])}</td><td>{_nl_num(s['precision'])}</td>"
        f"<td>{_nl_num(s['recall'])}</td><td>{_nl_num(s['f1'])}</td>"
    )


def _totals_row(
    label: str, entries: list[tuple[str, str, int, dict[str, Any]]], *, background: str
) -> str:
    """Aggregatierij: som van p/tp/fp/fn; P/R/F1 als gemiddelde over types met gold.

    De count-kolommen (p/tp/fp/fn) zijn opgeteld; P/R/F1 zijn het rekenkundig
    gemiddelde over de rijen mét gold (tp+fn>0). Nul-rijen (geen gold én geen
    voorspelling) tellen niet mee, anders zou het gemiddelde kunstmatig dalen.
    Zo valt het generale gemiddelde samen met de macro-F1 bovenaan.
    """
    p = sum(e[2] for e in entries)
    tp = sum(e[3]["tp"] for e in entries)
    fp = sum(e[3]["fp"] for e in entries)
    fn = sum(e[3]["fn"] for e in entries)
    scored = [e[3] for e in entries if e[3]["tp"] + e[3]["fn"] > 0]
    n = len(scored) or 1
    avg_p = round(sum(s["precision"] for s in scored) / n, 4)
    avg_r = round(sum(s["recall"] for s in scored) / n, 4)
    avg_f1 = round(sum(s["f1"] for s in scored) / n, 4)
    return (
        f'<tr style="font-weight:bold;background:{background}">'
        f"<td>{label}</td><td></td>"
        f"<td>{_nl_num(p)}</td><td>{_nl_num(tp)}</td><td>{_nl_num(fp)}</td><td>{_nl_num(fn)}</td>"
        f"<td>{_nl_num(avg_p)}</td><td>{_nl_num(avg_r)}</td><td>{_nl_num(avg_f1)}</td></tr>"
    )


def _format_generalization_failure(item: dict[str, Any]) -> str:
    """Mensleesbare failure-regel voor HTML/terminal."""
    reason = item.get("reason", "?")
    labels = {
        "entity_not_detected": "entity niet gedetecteerd",
        "wrong_entity_type": "verkeerd entity-type bij detectie",
        "wrong_generalized_form": "verkeerde gegeneraliseerde vorm",
    }
    label = labels.get(reason, reason)
    base = (
        f"{item['record']}: {item['original']} → verwacht {item['expected']!r} — {label}"
    )
    if reason == "wrong_entity_type":
        return (
            f"{base} (gedetecteerd: {item.get('detected_type')}, "
            f"verwacht type: {item.get('expected_type')})"
        )
    if reason == "wrong_generalized_form":
        return f"{base} (werkelijk: {item.get('actual')!r})"
    if reason == "entity_not_detected" and item.get("expected_type"):
        return f"{base} (verwacht type: {item.get('expected_type')})"
    return base


def _generalization_section(report: dict[str, Any]) -> str:
    """Detailblok generalisatie (BR-B01..B05) met mislukte checks."""
    gen = report.get("generalization") or {}
    summary = format_generalization_summary(gen)
    failures = gen.get("failures") or []
    if failures:
        fail_rows = "".join(
            f"<li>{html.escape(_format_generalization_failure(item))}</li>"
            for item in failures
        )
    else:
        fail_rows = "<li>geen mislukte checks</li>"
    return (
        f"<h2>Generalisatie (BR-B01..B05)</h2>"
        f"<p>Score: {summary}</p>"
        f"<ol>{fail_rows}</ol>"
    )


def _layer3_note(run_meta: dict[str, Any]) -> str:
    """Expliciete melding over de werkelijke status van laag 3 (de LLM-laag)."""
    model = run_meta.get("llm_model") or "onbekend"
    status = run_meta.get("llm_status")

    if not run_meta.get("use_llm") or status == "disabled":
        return (
            f'<p style="color:#D4A017;font-size:.9em"><strong>Let op: laag 3 (LLM '
            f"{model}) is in deze run NIET gedraaid.</strong> product- en "
            "projectdetectie via de LLM-laag ontbreekt daardoor.</p>"
        )
    if status == "ok":
        return (
            f'<p style="color:#256F8A;font-size:.9em"><strong>Laag 3 (LLM '
            f"{model}) is in deze run gedraaid.</strong></p>"
        )
    # use_llm aan, maar laag 3 leverde geen succesvolle run (bv. backend down).
    return (
        f'<p style="color:#A0263A;font-size:.9em"><strong>Let op: laag 3 (LLM '
        f"{model}) was ingeschakeld maar niet beschikbaar ({status}).</strong> "
        "product- en projectdetectie via de LLM-laag ontbreekt in deze run.</p>"
    )


def _score_rows(report: dict[str, Any]) -> str:
    """Bouw de scoretabel-rijen, gegroepeerd per direct/indirect met subtotalen."""
    overlap = report["scores"]["overlap"]["by_type"]
    layers_by_type = report.get("layers_by_type", {})
    predicted_by_type = report.get("predicted_by_type", {})
    run_meta = report.get("run_meta", {})
    detector_layers = report.get("detector_layers", {})

    # Toon álle EntityType-waarden, ook zuiver-nul-types, zodat ontbrekende
    # types expliciet zichtbaar blijven. (group, etype, laag-label, p, score)
    all_types = sorted(set(all_entity_types()) | set(overlap))
    entries: dict[str, list[tuple[str, str, int, dict[str, Any]]]] = {
        g: [] for g in _GROUP_ORDER
    }
    for etype in all_types:
        s = overlap.get(etype, _EMPTY_SCORE)
        observed = layers_by_type.get(etype, {})
        layer_label = (
            _format_observed(observed, run_meta)
            if observed
            else _format_expected(etype, run_meta, detector_layers)
        )
        p = predicted_by_type.get(etype, sum(observed.values()))
        entries[_group_of(etype)].append((etype, layer_label, p, s))

    html_rows: list[str] = []
    for group in _GROUP_ORDER:
        group_entries = entries[group]
        html_rows.append(
            f'<tr style="background:#eaeaea"><th colspan="9" style="text-align:left">'
            f"{_GROUP_LABELS[group]}</th></tr>"
        )
        for etype, layer_label, p, s in group_entries:
            # Bij indirect-identifiers de privacy-klasse achter het type tonen.
            short = _category_short(etype) if group == _GROUP_INDIRECT else ""
            type_cell = f"{etype} ({short})" if short else etype
            html_rows.append(
                f"<tr><td>{type_cell}</td><td>{layer_label}</td>{_score_cells(p, s)}</tr>"
            )
        html_rows.append(
            _totals_row(
                f"Som / Gemiddelde — {_GROUP_LABELS[group]}",
                group_entries,
                background="#f4f4f4",
            )
        )

    # Generale som/gemiddelde-rij over alle types samen.
    all_entries = [e for group in _GROUP_ORDER for e in entries[group]]
    html_rows.append(
        _totals_row("Generaal totaal (som / gemiddelde)", all_entries, background="#e2e2e2")
    )
    return "".join(html_rows)


def _heading(report: dict[str, Any]) -> str:
    """Kop met runtijd-tijdstempel, bv. 'Pylades Testharnas 2026-06-10 16:33'."""
    generated = report.get("generated_at")
    try:
        stamp = datetime.fromisoformat(generated).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"Pylades Testharnas run {stamp}"


_FOOTNOTE = (
    '<p style="color:#666;font-size:.85em">Kolommen (in volgorde):<br>'
    "<strong>type</strong> = entiteittype; bij indirect-identifiers staat de "
    "privacy-klasse erachter (quasi/clinical/free text).<br>"
    "<strong>laag</strong> = detectiemodel dat de voorspellingen leverde (bv. "
    '"spacy md, regex"). Bij een niet-voorspeld type staat welk model het '
    '<em>zou</em> detecteren met "(verwacht)"; "laag 3 niet gedraaid" als het '
    'type alleen via de optionele LLM-laag komt en die uit stond; "geen '
    'detector" als geen enkel model dit type dekt.<br>'
    "<strong>p</strong> = aantal voorspellingen van dit type (som over lagen).<br>"
    "<strong>tp</strong> = true positives: correct gedetecteerde gold-entities "
    "(overlap-match).<br>"
    "<strong>fp</strong> = false positives: voorspellingen zonder corresponderende "
    "gold-entity (over-detectie).<br>"
    "<strong>fn</strong> = false negatives: gemiste gold-entities (onder-detectie).<br>"
    "<strong>P</strong> = precision = tp / (tp + fp): aandeel van de voorspellingen "
    "dat correct is.<br>"
    "<strong>R</strong> = recall = tp / (tp + fn): aandeel van de gold-entities dat "
    "gevonden is.<br>"
    "<strong>F1</strong> = harmonisch gemiddelde van P en R.<br>"
    "<strong>Span-matching:</strong> de type-tabel en macro-F1 hieronder gebruiken "
    "<strong>overlap</strong> — tp bij zelfde type en overlappende span (grootste "
    "overlap wint). <strong>Exact</strong> telt alleen tp bij identieke "
    "<em>start</em>/<em>end</em> én type; dat is strenger en maakt "
    "randafwijkingen zichtbaar. In het vergelijkingsrapport staat exact als "
    "micro-F1 (exact), overlap als micro-F1 (overlap) en macro-F1 (overlap).<br>"
    "<strong>micro-F1</strong> (bovenaan) = P en R berekend uit de totalen van "
    "tp/fp/fn over álle types samen, daarna F1; hoog-volume types wegen daardoor "
    "zwaarder (overlap-modus).<br>"
    "<strong>macro-F1</strong> (bovenaan) = eerst F1 per type (alleen types met "
    "gold), daarna ongewogen gemiddeld; elk type weegt even zwaar (overlap-modus).<br>"
    "Het aantal gold-entities van een type is tp+fn; de som hiervan over de "
    "direct-groep is het totaal direct-identifiers bovenaan.<br>"
    "Rijen zijn gegroepeerd per direct- en indirect-identifier; elke groep eindigt "
    "met een som/gemiddelde-rij: p/tp/fp/fn opgeteld, P/R/F1 als gemiddelde over de "
    "types mét gold (nul-rijen tellen niet mee), zodat het generale gemiddelde "
    "samenvalt met de macro-F1 bovenaan.<br>"
    "<strong>Let op:</strong> &quot;direct-identifiers geleakt&quot; bovenaan is "
    "<em>niet</em> uit tp/fp/fn af te leiden — het is een aparte, span-dekking-"
    "gebaseerde KPI (een gold-identifier waarvan de originele tekst &lt;100% door "
    "detectie-spans wordt gedekt) en gelijk aan het aantal items in de lek-lijst. "
    "Het verschilt van fn: een gold die door een ánder-type-span wordt gemaskeerd "
    "telt als fn maar niet als lek, en een deels-overlappende juiste match telt als "
    "tp maar kan tóch lekken.</p>"
)


def write_html(report: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    leaks = report["leaks"]
    totals = report["totals"]
    latency = report["latency"]
    overlap_scores = report["scores"]["overlap"]
    heading = _heading(report)
    leak_rows = "".join(
        f"<li>{item['record']}: {item['type']} = {item['text']}</li>"
        for item in leaks["items"]
    ) or "<li>geen lekken</li>"
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>{heading}</title>
<style>body{{font-family:sans-serif;margin:2rem;}}table{{border-collapse:collapse;}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;}}</style></head>
<body>
<h1>{heading}</h1>
<p>Runner: {report['runner']} · records: {_nl_num(totals['records'])} · gold-entities:
{_nl_num(totals['gold_entities'])} · direct-identifiers:
{_nl_num(totals['direct_identifiers'])}</p>
{_performance_section(report)}
{_context_section(report)}
<h2>Leak-rate (primaire KPI)</h2>
<p>direct-identifiers geleakt: {_nl_num(leaks['direct_leaked'])} / {_nl_num(leaks['direct_total'])}
(leak-rate {_nl_num(leaks['leak_rate'])})</p>
<ol>{leak_rows}</ol>
{_generalization_section(report)}
<h2>Scores (overlap-matching)</h2>
<p>micro-F1: {_nl_num(overlap_scores['micro']['f1'])} ·
macro-F1: {_nl_num(overlap_scores['macro_f1'])}</p>
<table><tr><th>type</th><th>laag</th><th>p</th><th>tp</th><th>fp</th><th>fn</th>
<th>P</th><th>R</th><th>F1</th></tr>
{_score_rows(report)}</table>
{_FOOTNOTE}
{_layer3_note(report.get('run_meta', {}))}
<h2>Latency</h2>
<p>mean {_nl_num(latency['mean_ms'])} ms · p50 {_nl_num(latency['p50_ms'])} ms ·
p95 {_nl_num(latency['p95_ms'])} ms</p>
<p>{_warmup_note(latency)}</p>
</body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


def write_all(report: dict[str, Any], out_dir: str | Path) -> dict[str, Path]:
    stamp = _timestamp()
    base = Path(out_dir)
    prefix = f"{report['runner']}-{stamp}"
    return {
        "json": write_json(report, base / f"{prefix}.json"),
        "csv": write_csv(report, base / f"{prefix}.csv"),
        "html": write_html(report, base / f"{prefix}.html"),
    }
