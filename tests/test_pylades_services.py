"""Tests voor poort-parsing in scripts/pylades_services.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_pylades_services():
    path = Path(__file__).resolve().parents[1] / "scripts" / "pylades_services.py"
    spec = importlib.util.spec_from_file_location("pylades_services", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_lsof_pids_deduplicates_and_sorts() -> None:
    svc = _load_pylades_services()
    stdout = "6442\n6443\n6442\n"
    assert svc._parse_lsof_pids(stdout) == [6442, 6443]


def test_parse_netstat_pids_ignores_non_listening() -> None:
    svc = _load_pylades_services()
    stdout = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       1111
  TCP    127.0.0.1:8080         10.0.0.1:54321         ESTABLISHED     2222
  TCP    0.0.0.0:8501           0.0.0.0:0              LISTENING       3333
  TCP    [::]:8501              [::]:0                 LISTENING       4444
"""
    assert svc._parse_netstat_pids(stdout, 8080) == [1111]
    assert svc._parse_netstat_pids(stdout, 8501) == [3333, 4444]


def test_parse_netstat_pids_empty_on_no_match() -> None:
    svc = _load_pylades_services()
    stdout = "  TCP    127.0.0.1:9999         0.0.0.0:0              LISTENING       1\n"
    assert svc._parse_netstat_pids(stdout, 8080) == []
