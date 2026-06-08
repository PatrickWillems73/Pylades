"""CLI voor het eval-harnas.

Voorbeelden:
    python -m eval.cli bootstrap
    python -m eval.cli validate --dataset eval/datasets/bootstrap/dataset.jsonl
    python -m eval.cli run --dataset eval/datasets/bootstrap/dataset.jsonl
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.evaluate import evaluate
from eval.generators.bootstrap import main as bootstrap_main
from eval.manifest import verify_checksum
from eval.report import write_all
from eval.runners.base import Runner
from eval.runners.pylades_pipeline import PyladesPipelineRunner
from eval.schema import load_jsonl
from eval.validators import validate_dataset

_DEFAULT_DATASET = "eval/datasets/bootstrap/dataset.jsonl"
_DEFAULT_REPORT_DIR = "eval/reports"


def _build_runner(name: str) -> Runner:
    runners: dict[str, Runner] = {
        "pylades_md": PyladesPipelineRunner(name="pylades_md", use_llm=False),
        "pylades_md_llm": PyladesPipelineRunner(name="pylades_md_llm", use_llm=True),
    }
    if name not in runners:
        raise SystemExit(f"Onbekende runner {name!r}; kies uit: {', '.join(runners)}")
    return runners[name]


def _cmd_bootstrap(_args: argparse.Namespace) -> int:
    bootstrap_main()
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    records = load_jsonl(args.dataset)
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


def _cmd_run(args: argparse.Namespace) -> int:
    dataset_path = Path(args.dataset)
    manifest_path = dataset_path.parent / "manifest.json"
    if (
        manifest_path.exists()
        and not args.no_checksum
        and not verify_checksum(dataset_path, manifest_path)
    ):
        raise SystemExit(
            f"Checksum-mismatch: {dataset_path} wijkt af van {manifest_path}. "
            "Hergenereer de dataset of draai met --no-checksum."
        )

    records = load_jsonl(dataset_path)
    runner = _build_runner(args.runner)
    report = evaluate(records, runner)
    paths = write_all(report, args.report)

    leaks = report["leaks"]
    exposure = report["exposure"]
    clinical = exposure.get("clinical_sensitive", {})
    quasi = exposure.get("quasi_identifier", {})
    print(  # noqa: T201
        f"\nRunner: {report['runner']} · records: {report['totals']['records']}\n"
        f"Leak-rate (direct, GATE): {leaks['direct_leaked']}/{leaks['direct_total']} "
        f"= {leaks['leak_rate']}\n"
        f"Blootstelling clinical: {clinical.get('exposed', 0)}/{clinical.get('total', 0)} · "
        f"quasi: {quasi.get('exposed', 0)}/{quasi.get('total', 0)} (gerapporteerd, niet gegate)\n"
        f"micro-F1 exact: {report['scores']['exact']['micro']['f1']} · "
        f"overlap: {report['scores']['overlap']['micro']['f1']}\n"
        f"latency p50/p95: {report['latency']['p50_ms']}/{report['latency']['p95_ms']} ms\n"
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eval", description="Pylades detectie-eval-harnas")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("bootstrap", help="Genereer de offline bootstrap-dataset")

    p_val = sub.add_parser("validate", help="Valideer een dataset")
    p_val.add_argument("--dataset", default=_DEFAULT_DATASET)

    p_run = sub.add_parser("run", help="Draai een evaluatie en schrijf een rapport")
    p_run.add_argument("--dataset", default=_DEFAULT_DATASET)
    p_run.add_argument("--runner", default="pylades_md")
    p_run.add_argument("--report", default=_DEFAULT_REPORT_DIR)
    p_run.add_argument("--no-checksum", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "bootstrap": _cmd_bootstrap,
        "validate": _cmd_validate,
        "run": _cmd_run,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
