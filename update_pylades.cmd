@echo off
setlocal EnableDelayedExpansion

rem Dubbelklik op Windows: pull upstream van GitHub en sync lokale omgeving.
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
  echo git niet gevonden. Installeer Git for Windows: https://git-scm.com/download/win
  pause
  exit /b 1
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo Geen git-repository in %CD%
  pause
  exit /b 1
)

git status --porcelain | findstr /r "." >nul 2>&1
if not errorlevel 1 (
  echo Waarschuwing: je hebt lokale wijzigingen. Pull kan conflicten geven.
  set /p CONFIRM="Toch doorgaan? [j/N] "
  if /i not "!CONFIRM!"=="j" if /i not "!CONFIRM!"=="y" exit /b 1
)

for /f "delims=" %%b in ('git branch --show-current') do set BRANCH=%%b
for /f "delims=" %%h in ('git rev-parse HEAD') do set BEFORE=%%h

echo Pylades — bijwerken (tak: !BRANCH!^)…
git fetch origin
if errorlevel 1 (
  echo git fetch mislukt.
  pause
  exit /b 1
)

git rev-parse --abbrev-ref "@{u}" >nul 2>&1
if errorlevel 1 (
  echo Geen upstream ingesteld; pull van origin/!BRANCH!…
  git pull --ff-only origin !BRANCH!
) else (
  git pull --ff-only
)
if errorlevel 1 (
  echo git pull mislukt.
  pause
  exit /b 1
)

for /f "delims=" %%h in ('git rev-parse HEAD') do set AFTER=%%h
if "!BEFORE!"=="!AFTER!" (
  echo Al up-to-date.
) else (
  echo.
  echo Nieuwe commits:
  git log --oneline !BEFORE!..!AFTER!
)

where uv >nul 2>&1
if errorlevel 1 (
  echo.
  echo uv niet gevonden; sla dependency-sync over. Installeer uv: https://docs.astral.sh/uv/
  goto maybe_restart
)

echo.
echo Dependencies synchroniseren…
uv sync --extra dev
if errorlevel 1 (
  echo uv sync mislukt.
  pause
  exit /b 1
)

:maybe_restart
where uv >nul 2>&1
if errorlevel 1 goto done

echo.
set /p RESTART="Services herstarten met nieuwe code? [J/n] "
if /i "!RESTART!"=="n" goto done
uv run python scripts/pylades_services.py restart

:done
echo.
pause
