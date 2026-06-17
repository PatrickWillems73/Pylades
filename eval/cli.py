"""CLI voor het eval-harnas.

Voorbeelden (projectroot):
    python eval.py bootstrap
    python eval.py runners
    python eval.py validate --dataset eval/datasets/bootstrap/dataset.jsonl
    python eval.py run --dataset eval/datasets/bootstrap/dataset.jsonl

Alternatief: python -m eval.cli <commando>
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

from eval.compare import write_comparison
from eval.evaluate import evaluate
from eval.generators.bootstrap import main as bootstrap_main
from eval.generators.synthetic import generate_dataset
from eval.manifest import resolve_manifest, verify_checksum, write_manifest
from eval.metrics.generalization import format_generalization_summary
from eval.report import format_run_summary, write_all
from eval.runners.base import Runner
from eval.runners.mlx_backend import MLXLayer3Backend
from eval.runners.ner_backends import (
    DeduceBackend,
    GlinerBackend,
    NerBackendError,
    SpacyNerBackend,
)
from eval.runners.ner_pipeline import NerPipelineRunner
from eval.runners.ollama_mlx_backend import OllamaMlxEvalBackend
from eval.runners.pylades_pipeline import PyladesPipelineRunner
from eval.schema import dump_jsonl, load_jsonl
from eval.validators import validate_dataset
from proxy.detection import Layer3BackendError
from shared.config import settings

_DEFAULT_DATASET = "eval/datasets/bootstrap/dataset.jsonl"
_DEFAULT_DATASETS_DIR = "eval/datasets"
_DEFAULT_REPORT_DIR = "eval/reports"
_DEFAULT_RUNNER = "pylades_deduce_runtime"

# Voorkom HuggingFace-tokenizer-waarschuwingen na fork (bv. webbrowser.open_new).
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _runner_catalog() -> dict[str, tuple[str, Callable[[], Runner]]]:
    """Naam → (beschrijving, factory). Enige bron voor CLI en `--runner`."""
    return {
        "pylades_deduce_runtime": (
            "regex + DEDUCE runtime-pijplijn (laag 3 uit)",
            lambda: PyladesPipelineRunner(name="pylades_deduce_runtime", use_llm=False),
        ),
        "pylades_md_llm": (
            f"laag 3 via Ollama GGUF ({settings.ollama_model})",
            lambda: PyladesPipelineRunner(name="pylades_md_llm", use_llm=True),
        ),
        "pylades_md_ollama_mlx": (
            f"laag 3 via Ollama MLX ({settings.ollama_mlx_model}; OLLAMA_MLX=1)",
            lambda: PyladesPipelineRunner(
                name="pylades_md_ollama_mlx",
                use_llm=True,
                llm_backend=OllamaMlxEvalBackend(),
            ),
        ),
        "pylades_md_mlx": (
            f"laag 3 via mlx_lm.server ({settings.mlx_model})",
            lambda: PyladesPipelineRunner(
                name="pylades_md_mlx", use_llm=True, llm_backend=MLXLayer3Backend()
            ),
        ),
        # Fase 3 — laag-2 NER-modelvergelijking (regex + verwisselbare NER).
        "pylades_lg": (
            "regex + spaCy nl_core_news_lg (laag 2)",
            lambda: NerPipelineRunner(
                name="pylades_lg", backend=SpacyNerBackend("nl_core_news_lg")
            ),
        ),
        "pylades_gliner": (
            "regex + GLiNER multilingual PII (laag 2; eval-extra)",
            lambda: NerPipelineRunner(name="pylades_gliner", backend=GlinerBackend()),
        ),
        "pylades_deduce": (
            "regex + DEDUCE 3.x (laag 2; NerPipeline-vergelijking)",
            lambda: NerPipelineRunner(name="pylades_deduce", backend=DeduceBackend()),
        ),
    }


def _runner_names() -> list[str]:
    return list(_runner_catalog())


def _format_runners_list() -> str:
    lines: list[str] = []
    for name, (description, _) in _runner_catalog().items():
        default = " (default)" if name == _DEFAULT_RUNNER else ""
        lines.append(f"  {name}{default}")
        lines.append(f"    {description}")
    return "\n".join(lines)


def _emit_run_progress(current: int, total: int, label: str) -> None:
    """Toon voortgang op stderr (warm-up + per record)."""
    if label == "warm-up":
        msg = "Voortgang: warm-up (DEDUCE/LLM laden)…"
    else:
        msg = f"Voortgang: {current}/{total} · {label}…"
    if sys.stderr.isatty():
        print(f"{msg:<72}", file=sys.stderr, end="\r", flush=True)  # noqa: T201
        if current == total:
            print(file=sys.stderr)  # noqa: T201
    else:
        print(msg, file=sys.stderr, flush=True)  # noqa: T201


def _build_runner(name: str) -> Runner:
    catalog = _runner_catalog()
    if name not in catalog:
        raise SystemExit(
            f"Onbekende runner {name!r}; kies uit: {', '.join(catalog)}\n"
            f"Of: python eval.py runners"
        )
    return catalog[name][1]()


def _cmd_bootstrap(_args: argparse.Namespace) -> int:
    bootstrap_main()
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    dataset_path = out_dir / "dataset.jsonl"
    records = generate_dataset(args.n, model=args.model, seed=args.seed)

    report = validate_dataset(records)
    for warning in report.warnings:
        print(f"WAARSCHUWING: {warning}")  # noqa: T201
    if not report.ok:
        for error in report.errors:
            print(f"FOUT: {error}")  # noqa: T201
        raise SystemExit("Gegenereerde dataset is ongeldig; niet weggeschreven.")

    dump_jsonl(records, dataset_path)
    model_used = records[0].meta.get("model", "onbekend") if records else "onbekend"
    write_manifest(
        dataset_path,
        version=f"synthetic-{args.seed}",
        seed=args.seed,
        generator=f"synthetic.py via {model_used}",
        record_count=len(records),
        extra={"entity_count": report.entity_count},
    )
    print(  # noqa: T201
        f"Synthetische dataset geschreven: {dataset_path} "
        f"({len(records)} records, {report.entity_count} entities, model {model_used})"
    )
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    records = load_jsonl(_resolve_dataset_path(args.dataset))
    report = validate_dataset(records)
    for warning in report.warnings:
        print(f"WAARSCHUWING: {warning}")  # noqa: T201
    for error in report.errors:
        print(f"FOUT: {error}")  # noqa: T201
    print(  # noqa: T201
        f"{report.record_count} records, {report.entity_count} entities — "
        f"{'OK' if report.ok else 'ONGELDIG'}"
    )
    return 0 if report.ok else 1


def _resolve_dataset_path(arg: str) -> Path:
    """Sta een .jsonl-pad, een map, óf een groepsnaam toe als --dataset.

    Volgorde: bestaand bestand → `<map>/dataset.jsonl` → groepsnaam onder
    `eval/datasets/<naam>/dataset.jsonl`. Zo werkt zowel `--dataset synthetic`
    als het volledige pad. Geeft een duidelijke fout bij geen match.
    """
    p = Path(arg)
    if p.is_file():
        return p
    if p.is_dir() and (p / "dataset.jsonl").is_file():
        return p / "dataset.jsonl"
    grouped = Path(_DEFAULT_DATASETS_DIR) / arg / "dataset.jsonl"
    if grouped.is_file():
        return grouped
    raise SystemExit(
        f"Dataset niet gevonden: {arg!r}. Geef een .jsonl-pad, een map met dataset.jsonl, "
        f"of een groepsnaam onder {_DEFAULT_DATASETS_DIR}/ (bv. 'bootstrap', 'synthetic')."
    )


def _report_dir(report_base: str | Path, dataset_path: str | Path) -> Path:
    """Rapporten groeperen per datasetgroep: `<report_base>/<datasetmap-naam>/`.

    De groep is de naam van de map waarin de dataset staat (bv. `bootstrap`,
    `synthetic`). Zo komen rapporten consistent bij hun dataset te staan en
    botsen twee verschillende `dataset.jsonl` (onder bootstrap én synthetic)
    niet in dezelfde map. Staat de dataset losse in de root, dan valt het terug
    op de basismap zelf.
    """
    base = Path(report_base)
    group = Path(dataset_path).parent.name
    return base / group if group else base


def _cmd_runners(_args: argparse.Namespace) -> int:
    print(_format_runners_list())  # noqa: T201
    return 0


def _verify_dataset_checksum(
    dataset_path: Path, manifest_arg: str | None, *, no_checksum: bool
) -> None:
    """Verifieer de dataset-checksum tegen het (auto-gevonden) manifest."""
    if no_checksum:
        return
    manifest_path = Path(manifest_arg) if manifest_arg else resolve_manifest(dataset_path)
    if manifest_path is None:
        print(  # noqa: T201
            f"Let op: geen passend manifest gevonden voor {dataset_path} — "
            "checksum niet geverifieerd. Pin de dataset via `generate`/`bootstrap` "
            "of geef --manifest op."
        )
    elif not verify_checksum(dataset_path, manifest_path):
        raise SystemExit(
            f"Checksum-mismatch: {dataset_path} wijkt af van {manifest_path}. "
            "Hergenereer de dataset of draai met --no-checksum."
        )


def _cmd_run(args: argparse.Namespace) -> int:
    dataset_path = _resolve_dataset_path(args.dataset)
    _verify_dataset_checksum(dataset_path, args.manifest, no_checksum=args.no_checksum)

    records = load_jsonl(dataset_path)
    try:
        runner = _build_runner(args.runner)
    except (Layer3BackendError, NerBackendError) as exc:
        raise SystemExit(str(exc)) from None
    try:
        report = evaluate(
            records,
            runner,
            warmup=not args.no_warmup,
            on_progress=_emit_run_progress,
        )
    except (Layer3BackendError, NerBackendError) as exc:
        raise SystemExit(str(exc)) from None
    paths = write_all(report, _report_dir(args.report, dataset_path))

    if not args.no_open:
        # Open het HTML-rapport in een nieuw browservenster zodra het klaar is.
        webbrowser.open_new(paths["html"].resolve().as_uri())

    leaks = report["leaks"]
    exposure = report["exposure"]
    clinical = exposure.get("clinical_sensitive", {})
    quasi = exposure.get("quasi_identifier", {})
    latency = report["latency"]
    if latency["warmup"]:
        warmup_note = f"na warm-up ({latency['warmup_ms']} ms weggegooid)"
    else:
        warmup_note = "geen warm-up (incl. cold-start)"
    print(  # noqa: T201
        f"\n{format_run_summary(report)}\n"
        f"Leak-rate (direct, GATE): {leaks['direct_leaked']}/{leaks['direct_total']} "
        f"= {leaks['leak_rate']}\n"
        f"Blootstelling clinical: {clinical.get('exposed', 0)}/{clinical.get('total', 0)} · "
        f"quasi: {quasi.get('exposed', 0)}/{quasi.get('total', 0)} (gerapporteerd, niet gegate)\n"
        f"micro-F1 exact: {report['scores']['exact']['micro']['f1']} · "
        f"overlap: {report['scores']['overlap']['micro']['f1']}\n"
        f"latency p50/p95: {latency['p50_ms']}/{latency['p95_ms']} ms ({warmup_note})\n"
        f"Rapport: {paths['json']}"
    )
    if leaks["items"]:
        print("Gelekte direct-identifiers:")  # noqa: T201
        for item in leaks["items"]:
            print(  # noqa: T201
                f"  - {item['record']}: {item['type']} = {item['text']} "
                f"({item['severity']}, dekking {item['coverage']})"
            )
    return 0


_DEFAULT_COMPARE_RUNNERS = "pylades_deduce_runtime,pylades_lg,pylades_gliner,pylades_deduce"


def _cmd_compare(args: argparse.Namespace) -> int:
    dataset_path = _resolve_dataset_path(args.dataset)
    _verify_dataset_checksum(dataset_path, args.manifest, no_checksum=args.no_checksum)
    records = load_jsonl(dataset_path)

    names = [n.strip() for n in args.runners.split(",") if n.strip()]
    out_dir = _report_dir(args.report, dataset_path)
    reports: list[dict] = []
    runner_html: dict[str, Path] = {}
    for name in names:
        try:
            runner = _build_runner(name)
            report = evaluate(
                records, runner, warmup=not args.no_warmup, on_progress=_emit_run_progress
            )
        except (Layer3BackendError, NerBackendError) as exc:
            # Eén ontbrekend model mag de vergelijking niet blokkeren: sla over.
            print(f"Overslaan {name}: {exc}", file=sys.stderr)  # noqa: T201
            continue
        paths = write_all(report, out_dir)
        runner_html[report["runner"]] = paths["html"]
        reports.append(report)

    if not reports:
        raise SystemExit("Geen enkele runner kon draaien; niets te vergelijken.")

    paths = write_comparison(reports, out_dir, runner_html=runner_html)
    if not args.no_open:
        webbrowser.open_new(paths["html"].resolve().as_uri())
    gen_lines = "\n".join(
        f"  {r['runner']}: generalisatie BR-B "
        f"{format_generalization_summary(r.get('generalization'))}"
        for r in reports
    )
    print(  # noqa: T201
        f"\nVergeleken: {', '.join(r['runner'] for r in reports)}\n"
        f"{gen_lines}\n"
        f"Vergelijkingsrapport: {paths['html']}\n{paths['csv']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval", description="Pylades detectie-eval-harnas")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Genereer de offline bootstrap-dataset")

    p_gen = sub.add_parser("generate", help="Genereer synthetische dossiers via de Anthropic-API")
    p_gen.add_argument("--n", type=int, default=10)
    p_gen.add_argument("--model", default=None, help="Anthropic model-id; default: nieuwste opus")
    p_gen.add_argument("--seed", type=int, default=1)
    p_gen.add_argument("--out", default="eval/datasets/synthetic")

    _dataset_help = (
        "Pad naar .jsonl, een map met dataset.jsonl, of een groepsnaam "
        "(bv. 'bootstrap', 'synthetic')."
    )
    p_val = sub.add_parser("validate", help="Valideer een dataset")
    p_val.add_argument("--dataset", default=_DEFAULT_DATASET, help=_dataset_help)

    sub.add_parser("runners", help="Toon beschikbare eval-runners (--runner)")

    p_run = sub.add_parser("run", help="Draai een evaluatie en schrijf een rapport")
    p_run.add_argument("--dataset", default=_DEFAULT_DATASET, help=_dataset_help)
    p_run.add_argument(
        "--runner",
        default=_DEFAULT_RUNNER,
        choices=_runner_names(),
        help="Model-adapter; zie ook: python eval.py runners",
    )
    p_run.add_argument(
        "--report",
        default=_DEFAULT_REPORT_DIR,
        help="Basismap; rapporten komen in <report>/<datasetgroep>/ (bv. .../synthetic/).",
    )
    p_run.add_argument(
        "--manifest",
        default=None,
        help="Expliciet manifest-pad; default: automatisch zoeken op dataset_file-match.",
    )
    p_run.add_argument("--no-checksum", action="store_true")
    p_run.add_argument(
        "--no-warmup",
        action="store_true",
        help="Sla de warm-up over; meet de cold-start mee in de latency-percentielen.",
    )
    p_run.add_argument(
        "--no-open",
        action="store_true",
        help="Open het HTML-rapport niet automatisch in de browser.",
    )

    p_cmp = sub.add_parser(
        "compare",
        help="Draai meerdere runners op dezelfde dataset en schrijf een vergelijkend rapport",
    )
    p_cmp.add_argument("--dataset", default=_DEFAULT_DATASET, help=_dataset_help)
    p_cmp.add_argument(
        "--runners",
        default=_DEFAULT_COMPARE_RUNNERS,
        help=(
            "Komma-gescheiden runner-namen (default: de NER-vergelijkingsset). "
            "Ontbrekende modellen worden overgeslagen."
        ),
    )
    p_cmp.add_argument(
        "--report",
        default=_DEFAULT_REPORT_DIR,
        help="Basismap; rapporten komen in <report>/<datasetgroep>/.",
    )
    p_cmp.add_argument("--manifest", default=None, help="Expliciet manifest-pad.")
    p_cmp.add_argument("--no-checksum", action="store_true")
    p_cmp.add_argument("--no-warmup", action="store_true")
    p_cmp.add_argument(
        "--no-open", action="store_true", help="Open het HTML-rapport niet automatisch."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "bootstrap": _cmd_bootstrap,
        "generate": _cmd_generate,
        "validate": _cmd_validate,
        "runners": _cmd_runners,
        "run": _cmd_run,
        "compare": _cmd_compare,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
