#!/bin/bash
# Dubbelklik op macOS: herstart proxy + Streamlit en open de webapp in je browser.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv niet gevonden. Installeer uv: https://docs.astral.sh/uv/" >&2
  read -r -p "Druk Enter om te sluiten…"
  exit 1
fi

echo "Pylades — services herstarten…"
uv run --no-sync python scripts/pylades_services.py restart

UI_PORT="$(uv run --no-sync python -c "from shared.config import settings; print(settings.ui_port)")"
APP_URL="http://127.0.0.1:${UI_PORT}"
HEALTH_URL="${APP_URL}/_stcore/health"

echo "Wachten tot Streamlit bereikbaar is…"
ready=0
for _ in $(seq 1 60); do
  if curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done

if [[ "$ready" -eq 1 ]]; then
  echo "Browser openen: ${APP_URL}"
  open "$APP_URL"
else
  echo "Streamlit reageert nog niet op ${HEALTH_URL}." >&2
  echo "Probeer handmatig: open \"${APP_URL}\"" >&2
fi

echo ""
echo "Services draaien op de achtergrond (logs: logs/proxy.log, logs/streamlit.log)."
echo "Stoppen: uv run python scripts/pylades_services.py stop"
echo ""
read -r -p "Druk Enter om dit venster te sluiten…"
