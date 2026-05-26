#!/usr/bin/env python3
"""Start/stop/herstart de Pylades-proxy en Streamlit-UI (lokale dev).

Gebruik vanaf de projectroot:

    uv run python scripts/pylades_services.py restart
    uv run python scripts/pylades_services.py stop
    uv run python scripts/pylades_services.py start
    uv run python scripts/pylades_services.py status

Poorten komen uit `shared.config.settings` (.env: PROXY_PORT / UI_PORT).
Standaard: proxy 8080, UI 8501.

Platform:
- macOS/Linux: `lsof` voor poortdetectie
- Windows: ingebouwde `netstat` + `taskkill` voor geforceerd stoppen
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _repo_root() -> Path:
    start = Path(__file__).resolve().parent
    for base in (start, *start.parents):
        if (base / "pyproject.toml").is_file():
            return base
    return Path.cwd()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _parse_lsof_pids(stdout: str) -> list[int]:
    pids: list[int] = []
    for line in stdout.strip().splitlines():
        part = line.strip()
        if part.isdigit():
            pids.append(int(part))
    return sorted(set(pids))


def _parse_netstat_pids(stdout: str, port: int) -> list[int]:
    """Parse `netstat -ano -p TCP` output; retourneer LISTENING PIDs op `port`."""
    suffix = f":{port}"
    pids: list[int] = []
    for line in stdout.splitlines():
        upper = line.upper()
        if "LISTENING" not in upper:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        if not local_addr.endswith(suffix):
            continue
        pid_str = parts[-1]
        if pid_str.isdigit():
            pids.append(int(pid_str))
    return sorted(set(pids))


def _pids_on_port_unix(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print(
            "Geen `lsof` gevonden; installeer lsof of stop processen handmatig.",
            file=sys.stderr,
        )
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return _parse_lsof_pids(result.stdout)


def _pids_on_port_windows(port: int) -> list[int]:
    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    return _parse_netstat_pids(result.stdout, port)


def _pids_on_port(port: int) -> list[int]:
    if _is_windows():
        return _pids_on_port_windows(port)
    return _pids_on_port_unix(port)


def _terminate_pid(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.kill(pid, signal.SIGTERM)


def _force_kill_pid(pid: int) -> None:
    if _is_windows():
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def _kill_pids(pids: list[int], *, graceful_secs: float = 1.0) -> None:
    my_pid = os.getpid()
    for pid in pids:
        if pid == my_pid:
            continue
        _terminate_pid(pid)
    deadline = time.monotonic() + graceful_secs
    remaining = [p for p in pids if p != my_pid]
    while remaining and time.monotonic() < deadline:
        time.sleep(0.1)
        remaining = [pid for pid in remaining if _pid_alive(pid)]
    for pid in remaining:
        _force_kill_pid(pid)


def _stop_ports(ports: tuple[int, int]) -> None:
    seen: set[int] = set()
    for port in ports:
        for pid in _pids_on_port(port):
            seen.add(pid)
    if not seen:
        print("Geen luisterende processen op de Pylades-poorten.")
        return
    print(f"Stoppen PID(s): {sorted(seen)} …")
    _kill_pids(sorted(seen))
    print("Gestopt.")


def _any_listener(port: int) -> bool:
    return bool(_pids_on_port(port))


def _spawn(
    cmd: list[str],
    *,
    cwd: Path,
    log_path: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as log_f:
        popen_kwargs: dict[str, object] = {
            "cwd": cwd,
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
        }
        if _is_windows():
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603


def _sync_ui_favicon(root: Path) -> None:
    """Tabblad laadt `./favicon.png` uit Streamlits package-static, niet uit `.streamlit/`."""
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    try:
        from ui.favicon_sync import sync_streamlit_favicon  # noqa: PLC0415
    except ImportError:
        print("Waarschuwing: ui.favicon_sync niet gevonden; tab-favicon blijft Streamlit-default.")
        return
    dst = sync_streamlit_favicon()
    if dst is not None:
        print(f"Tab-favicon gesynchroniseerd → {dst}")


def _start_services(proxy_port: int, ui_port: int, root: Path) -> None:
    _sync_ui_favicon(root)
    py = sys.executable
    logs = root / "logs"
    proxy_log = logs / "proxy.log"
    ui_log = logs / "streamlit.log"

    proxy_cmd = [
        py,
        "-m",
        "uvicorn",
        "proxy.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(proxy_port),
    ]
    ui_cmd = [
        py,
        "-m",
        "streamlit",
        "run",
        "ui/Home.py",
        "--server.port",
        str(ui_port),
        "--server.address",
        "127.0.0.1",
    ]

    _spawn(proxy_cmd, cwd=root, log_path=proxy_log)
    _spawn(ui_cmd, cwd=root, log_path=ui_log)

    print("Gestart:")
    print(f"  Proxy:    http://127.0.0.1:{proxy_port}/healthz")
    print(f"  Streamlit: http://127.0.0.1:{ui_port}")
    print(f"  Logbestanden: {proxy_log} , {ui_log}")


def _http_ping(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _cmd_status(proxy_port: int, ui_port: int) -> None:
    proxy_ok = _http_ping(f"http://127.0.0.1:{proxy_port}/healthz")
    ui_ok = _http_ping(f"http://127.0.0.1:{ui_port}/_stcore/health")
    print(f"Proxy (:{proxy_port}):     {'OK' if proxy_ok else 'niet bereikbaar'}")
    print(f"Streamlit (:{ui_port}): {'OK' if ui_ok else 'niet bereikbaar'}")


def main() -> None:
    root = _repo_root()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    os.chdir(root)

    from shared.config import settings  # noqa: PLC0415 — na chdir voor .env-pad

    proxy_port = settings.proxy_port
    ui_port = settings.ui_port
    ports = (proxy_port, ui_port)

    parser = argparse.ArgumentParser(
        description="Pylades — proxy en Streamlit starten/stoppen/herstarten.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="restart",
        choices=("restart", "stop", "start", "status"),
        help="restart (default), stop, start of status",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bij start: maak poorten eerst vrij (zelfde als impliciet bij restart).",
    )
    args = parser.parse_args()

    if args.command == "status":
        _cmd_status(proxy_port, ui_port)
        return

    if args.command == "stop":
        _stop_ports(ports)
        return

    if args.command == "restart":
        _stop_ports(ports)
        time.sleep(0.3)
        _start_services(proxy_port, ui_port, root)
        return

    # start
    if args.force:
        _stop_ports(ports)
        time.sleep(0.3)
    elif _any_listener(proxy_port) or _any_listener(ui_port):
        print(
            "Er luistert al iets op de proxy- of UI-poort. "
            "Gebruik `restart`, `stop`, of `start --force`.",
            file=sys.stderr,
        )
        sys.exit(1)
    _start_services(proxy_port, ui_port, root)


if __name__ == "__main__":
    main()
