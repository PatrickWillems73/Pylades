#!/usr/bin/env python3
"""Pylades detectie-eval-harnas — CLI vanaf de projectroot.

Voorbeelden:

    uv run python eval.py bootstrap
    uv run python eval.py generate --n 10 --out eval/datasets/synthetic
    uv run python eval.py validate --dataset eval/datasets/bootstrap/dataset.jsonl
    uv run python eval.py run --dataset eval/datasets/bootstrap/dataset.jsonl

Zie TESTPLAN.md voor doel en architectuur. Implementatie: eval/cli.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

for _root in Path(__file__).resolve().parents:
    if (_root / "pyproject.toml").is_file():
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        break

from eval.cli import main as eval_main


def main() -> int:
    return eval_main()


if __name__ == "__main__":
    raise SystemExit(main())
