"""Vergelijkend rapport over meerdere model-adapters (TESTPLAN.md fase 3).

Draait dezelfde dataset door meerdere runners en zet de kerncijfers naast
elkaar: PRF (micro/macro), de privacy-KPI (direct-leak-rate) en latency. Zo
ondersteunt het rapport doel 3 — het optimale NER-model kiezen — in één
overzicht. De losse per-runner-rapporten (JSON/CSV/HTML) worden óók geschreven
voor detailanalyse.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.report import _heading, _nl_num, _timestamp

# Kolommen van het vergelijkingsrapport: (sleutelpad, kop). Het sleutelpad wordt
# via `_dig` opgehaald uit het per-runner-rapport.
_COLUMNS: list[tuple[tuple[str, ...], str]] = [
    (("runner",), "runner"),
    (("layers_config", "layer2"), "laag 2 (NER)"),
    (("totals", "records"), "records"),
    (("scores", "exact", "micro", "f1"), "micro-F1 (exact)"),
    (("scores", "overlap", "micro", "f1"), "micro-F1 (overlap)"),
    (("scores", "overlap", "macro_f1"), "macro-F1 (overlap)"),
    (("performance", "direct_identifier_macro_f1"), "direct macro-F1"),
    (("performance", "indirect_identifier_macro_f1"), "indirect macro-F1"),
    (("leaks", "direct_leaked"), "direct geleakt"),
    (("leaks", "direct_total"), "direct totaal"),
    (("leaks", "leak_rate"), "leak-rate"),
    (("over_redaction",), "over-redactie"),
    (("latency", "p50_ms"), "p50 (ms)"),
    (("latency", "p95_ms"), "p95 (ms)"),
]


def _dig(report: dict[str, Any], path: tuple[str, ...]) -> Any:
    cur: Any = report
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key, "")
    return cur


def write_comparison_csv(reports: list[dict[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([head for _path, head in _COLUMNS])
        for report in reports:
            writer.writerow([_dig(report, p) for p, _head in _COLUMNS])
    return out


def write_comparison_html(
    reports: list[dict[str, Any]],
    path: str | Path,
    *,
    runner_html: dict[str, Path] | None = None,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    except ValueError:  # pragma: no cover - strftime faalt niet realistisch
        stamp = ""
    heading = f"Pylades NER-vergelijking run {stamp}"
    env = reports[0].get("environment", {}) if reports else {}
    html_by_runner = runner_html or {}

    header_cells = "".join(f"<th>{head}</th>" for _path, head in _COLUMNS)
    body_rows = ""
    for report in reports:
        runner = str(report.get("runner", "?"))
        detail_html = html_by_runner.get(runner)
        cells: list[str] = []
        for col_path, _head in _COLUMNS:
            if col_path == ("runner",):
                if detail_html is not None:
                    cells.append(f'<td><a href="{detail_html.name}">{runner}</a></td>')
                else:
                    cells.append(f"<td>{runner}</td>")
            else:
                cells.append(f"<td>{_nl_num(_dig(report, col_path))}</td>")
        body_rows += f"<tr>{''.join(cells)}</tr>"

    runner_links = ""
    for report in reports:
        runner = str(report.get("runner", "?"))
        title = _heading(report)
        detail_html = html_by_runner.get(runner)
        if detail_html is not None:
            label = f'<a href="{detail_html.name}">{runner}</a>'
        else:
            label = runner
        runner_links += f"<li>{label} — {title}</li>"
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>{heading}</title>
<style>body{{font-family:sans-serif;margin:2rem;}}table{{border-collapse:collapse;}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;text-align:right;}}
td:first-child,th:first-child,td:nth-child(2),th:nth-child(2){{text-align:left;}}</style>
</head><body>
<h1>{heading}</h1>
<p>Hardware: {env.get('summary', 'onbekend')} · Python {env.get('python', '?')}</p>
<p>Zelfde dataset, identieke regex-laag 1 + outbound-maskering; alleen de
laag-2 NER-backend verschilt per rij.</p>
<table><tr>{header_cells}</tr>{body_rows}</table>
<p style="color:#666;font-size:.85em"><strong>Span-matching:</strong> bij
<strong>F1 (exact)</strong> telt een voorspelling alleen als tp als type,
<em>start</em> en <em>end</em> exact overeenkomen met de gold-entity; bij
<strong>F1 (overlap)</strong> volstaat hetzelfde type plus een overlappende
span (bij meerdere kandidaten wint de grootste overlap). Overlap is minder
streng en maakt randafwijkingen zichtbaar zonder de entiteit als gemist te
tellen.<br>
<strong>micro-F1 (exact/overlap)</strong> = P en R uit de opgetelde tp/fp/fn
over álle types, daarna F1; hoog-volume types wegen zwaarder.<br>
<strong>macro-F1 (overlap)</strong> = eerst F1 per type (alleen types met
gold), daarna ongewogen gemiddeld; alleen overlap wordt hier getoond (exact
staat in het per-runner JSON).<br>
<strong>direct geleakt</strong> is de primaire privacy-KPI (harde gate: 0).
Lagere latency is beter; modellen zijn één-voor-één gemeten (M1
8&nbsp;GB-constraint).</p>
<h2>Per-runner-rapporten</h2><ul>{runner_links}</ul>
</body></html>"""
    out.write_text(html, encoding="utf-8")
    return out


def write_comparison(
    reports: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    runner_html: dict[str, Path] | None = None,
) -> dict[str, Path]:
    base = Path(out_dir)
    prefix = f"compare-{_timestamp()}"
    return {
        "csv": write_comparison_csv(reports, base / f"{prefix}.csv"),
        "html": write_comparison_html(
            reports, base / f"{prefix}.html", runner_html=runner_html
        ),
    }
