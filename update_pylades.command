#!/bin/bash
# Dubbelklik op macOS: pull upstream van GitHub en sync lokale omgeving.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if ! command -v git >/dev/null 2>&1; then
  echo "git niet gevonden. Installeer Xcode Command Line Tools of Git." >&2
  read -r -p "Druk Enter om te sluiten…"
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Geen git-repository in ${ROOT}" >&2
  read -r -p "Druk Enter om te sluiten…"
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Waarschuwing: je hebt lokale wijzigingen. Pull kan conflicten geven." >&2
  read -r -p "Toch doorgaan? [j/N] " confirm
  case "${confirm}" in
    [jJyY]) ;;
    *) exit 1 ;;
  esac
fi

BRANCH="$(git branch --show-current)"
BEFORE="$(git rev-parse HEAD)"

echo "Pylades — bijwerken (tak: ${BRANCH})…"
git fetch origin

if git rev-parse --abbrev-ref "@{u}" >/dev/null 2>&1; then
  git pull --ff-only
else
  echo "Geen upstream ingesteld; pull van origin/${BRANCH}…"
  git pull --ff-only origin "${BRANCH}"
fi

AFTER="$(git rev-parse HEAD)"
if [[ "${BEFORE}" != "${AFTER}" ]]; then
  echo ""
  echo "Nieuwe commits:"
  git log --oneline "${BEFORE}..${AFTER}"
else
  echo "Al up-to-date."
fi

if command -v uv >/dev/null 2>&1; then
  echo ""
  echo "Dependencies synchroniseren…"
  uv sync --extra dev
else
  echo ""
  echo "uv niet gevonden; sla dependency-sync over. Installeer uv: https://docs.astral.sh/uv/" >&2
fi

if command -v uv >/dev/null 2>&1; then
  echo ""
  read -r -p "Services herstarten met nieuwe code? [J/n] " restart
  case "${restart}" in
    [nN]) ;;
    *)
      uv run python scripts/pylades_services.py restart
      ;;
  esac
fi

echo ""
read -r -p "Druk Enter om dit venster te sluiten…"
