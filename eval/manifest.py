"""Manifest-helpers voor gepinde datasets.

Een dataset is pas her-uitvoerbaar als hij **gepind** is: een `manifest.json`
naast de JSONL legt de sha256-checksum, het aantal records, de seed en de
generator-herkomst vast. Bij een run controleren we de checksum zodat een
stilzwijgend gewijzigde dataset niet ongemerkt andere cijfers oplevert.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha or None


def write_manifest(
    dataset_path: str | Path,
    *,
    version: str,
    seed: int,
    generator: str,
    record_count: int,
    extra: dict[str, Any] | None = None,
) -> Path:
    dataset = Path(dataset_path)
    manifest = {
        "version": version,
        "dataset_file": dataset.name,
        "sha256": sha256_file(dataset),
        "record_count": record_count,
        "seed": seed,
        "generator": generator,
        "git_sha": _git_sha(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        manifest.update(extra)
    manifest_path = dataset.parent / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def read_manifest(manifest_path: str | Path) -> dict[str, Any]:
    return json.loads(Path(manifest_path).read_text(encoding="utf-8"))


def resolve_manifest(dataset_path: str | Path) -> Path | None:
    """Vind het manifest dat bij deze dataset hoort (match op `dataset_file`).

    Meerdere datasets kunnen in dezelfde map staan; we kiezen het manifest
    waarvan het `dataset_file`-veld exact deze dataset noemt. Zo botst
    `dataset-10dossiers.jsonl` nooit tegen het `manifest.json` van een ander
    bestand. Conventies (op volgorde van voorkeur): `<stem>.manifest.json`, de
    `dataset`→`manifest`-naamswap (`manifest-10dossiers.json`), en `manifest.json`.
    """
    dataset = Path(dataset_path)
    swapped = dataset.parent / (
        dataset.name.replace("dataset", "manifest", 1).removesuffix(".jsonl") + ".json"
    )
    candidates = [
        dataset.with_suffix(".manifest.json"),
        swapped,
        dataset.parent / "manifest.json",
    ]
    for cand in candidates:
        if not cand.exists():
            continue
        try:
            meta = read_manifest(cand)
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("dataset_file") == dataset.name:
            return cand
    return None


def verify_checksum(dataset_path: str | Path, manifest_path: str | Path) -> bool:
    """True als de dataset-checksum overeenkomt met het manifest."""
    manifest = read_manifest(manifest_path)
    return manifest.get("sha256") == sha256_file(dataset_path)
