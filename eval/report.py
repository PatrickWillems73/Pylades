"""Schrijf een evaluatie-rapport weg als JSON, CSV en een compacte HTML.

Stdlib-only (json/csv): geen extra dependencies voor de basisrapportage. De
bestandsnamen krijgen een timestamp zodat opeenvolgende runs naast elkaar
bewaard blijven voor vergelijking.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from eval.evaluate import all_entity_types


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


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
    types = sorted(set(exact) | set(overlap)) or all_entity_types()
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "entity_type",
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
                    etype,
                    e.get("tp", 0), e.get("fp", 0), e.get("fn", 0),
                    e.get("precision", 0), e.get("recall", 0), e.get("f1", 0),
                    o.get("tp", 0), o.get("fp", 0), o.get("fn", 0),
                    o.get("precision", 0), o.get("recall", 0), o.get("f1", 0),
                ]
            )
    return out


def write_html(report: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    leaks = report["leaks"]
    rows = []
    overlap = report["scores"]["overlap"]["by_type"]
    for etype in sorted(overlap):
        s = overlap[etype]
        rows.append(
            f"<tr><td>{etype}</td><td>{s['tp']}</td><td>{s['fp']}</td>"
            f"<td>{s['fn']}</td><td>{s['precision']}</td><td>{s['recall']}</td>"
            f"<td>{s['f1']}</td></tr>"
        )
    leak_rows = "".join(
        f"<li>{item['record']}: {item['type']} = {item['text']}</li>"
        for item in leaks["items"]
    ) or "<li>geen lekken</li>"
    html = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>Pylades eval — {report['runner']}</title>
<style>body{{font-family:sans-serif;margin:2rem;}}table{{border-collapse:collapse;}}
td,th{{border:1px solid #ccc;padding:.3rem .6rem;}}</style></head>
<body>
<h1>Pylades eval — {report['runner']}</h1>
<p>Records: {report['totals']['records']} · gold-entities:
{report['totals']['gold_entities']} · direct-identifiers:
{report['totals']['direct_identifiers']}</p>
<h2>Leak-rate (primaire KPI)</h2>
<p>direct geleakt: {leaks['direct_leaked']} / {leaks['direct_total']}
(leak-rate {leaks['leak_rate']})</p>
<ul>{leak_rows}</ul>
<h2>Scores (overlap-matching)</h2>
<p>micro-F1: {report['scores']['overlap']['micro']['f1']} ·
macro-F1: {report['scores']['overlap']['macro_f1']}</p>
<table><tr><th>type</th><th>tp</th><th>fp</th><th>fn</th><th>P</th><th>R</th><th>F1</th></tr>
{''.join(rows)}</table>
<h2>Latency</h2>
<p>mean {report['latency']['mean_ms']} ms · p50 {report['latency']['p50_ms']} ms ·
p95 {report['latency']['p95_ms']} ms</p>
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
