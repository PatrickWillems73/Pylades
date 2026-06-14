"""Regressietests voor CLI-padlogica: rapportgroepering + manifest-resolutie."""

from __future__ import annotations

from pathlib import Path

import pytest

from eval.cli import _format_runners_list, _report_dir, _resolve_dataset_path, main
from eval.manifest import resolve_manifest, write_manifest


def test_report_dir_groups_by_dataset_folder() -> None:
    assert _report_dir("eval/reports", "eval/datasets/synthetic/dataset.jsonl") == Path(
        "eval/reports/synthetic"
    )
    assert _report_dir("eval/reports", "eval/datasets/bootstrap/dataset.jsonl") == Path(
        "eval/reports/bootstrap"
    )


def test_report_dir_falls_back_to_base_for_bare_path() -> None:
    assert _report_dir("eval/reports", "dataset.jsonl") == Path("eval/reports")


def _make_dataset(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_resolve_manifest_matches_on_dataset_file(tmp_path: Path) -> None:
    # Twee datasets in dezelfde map met elk een eigen manifest; resolutie moet
    # het juiste manifest kiezen op basis van het dataset_file-veld.
    ds_a = tmp_path / "dataset.jsonl"
    ds_b = tmp_path / "dataset-10dossiers.jsonl"
    _make_dataset(ds_a, '{"a": 1}\n')
    _make_dataset(ds_b, '{"b": 2}\n')

    write_manifest(ds_a, version="v", seed=0, generator="t", record_count=1)
    # write_manifest schrijft altijd manifest.json; bewaar het tweede onder een
    # gesuffixte naam zoals in de praktijk.
    man_a = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    write_manifest(ds_b, version="v", seed=0, generator="t", record_count=1)
    (tmp_path / "manifest-10dossiers.json").write_text(
        (tmp_path / "manifest.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Herstel manifest.json voor dataset.jsonl (write_manifest overschreef het).
    (tmp_path / "manifest.json").write_text(man_a, encoding="utf-8")

    assert resolve_manifest(ds_a) == tmp_path / "manifest.json"
    assert resolve_manifest(ds_b) == tmp_path / "manifest-10dossiers.json"


def test_resolve_dataset_accepts_file_dir_and_group() -> None:
    # Volledig pad, map met dataset.jsonl, en kale groepsnaam wijzen alle drie
    # naar een bestaand bestand.
    assert _resolve_dataset_path("eval/datasets/bootstrap/dataset.jsonl") == Path(
        "eval/datasets/bootstrap/dataset.jsonl"
    )
    assert _resolve_dataset_path("eval/datasets/bootstrap") == Path(
        "eval/datasets/bootstrap/dataset.jsonl"
    )
    assert _resolve_dataset_path("bootstrap") == Path("eval/datasets/bootstrap/dataset.jsonl")


def test_resolve_dataset_raises_on_unknown() -> None:
    with pytest.raises(SystemExit):
        _resolve_dataset_path("bestaat-niet")


def test_resolve_manifest_none_when_no_match(tmp_path: Path) -> None:
    ds = tmp_path / "dataset-renamed.jsonl"
    _make_dataset(ds, '{"x": 1}\n')
    # Manifest verwijst naar een andere bestandsnaam → geen match.
    other = tmp_path / "dataset.jsonl"
    _make_dataset(other, '{"y": 2}\n')
    write_manifest(other, version="v", seed=0, generator="t", record_count=1)

    assert resolve_manifest(ds) is None


def test_runners_lists_all_adapters() -> None:
    text = _format_runners_list()
    for name in ("pylades_md", "pylades_md_llm", "pylades_md_ollama_mlx", "pylades_md_mlx"):
        assert name in text
    assert "(default)" in text


def test_runners_command_prints_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["runners"]) == 0
    out = capsys.readouterr().out
    assert "pylades_md_ollama_mlx" in out
