@echo off
setlocal EnableDelayedExpansion

rem Dubbelklik op Windows: herstart proxy + Streamlit en open de webapp in je browser.
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
  echo uv niet gevonden. Installeer uv: https://docs.astral.sh/uv/
  pause
  exit /b 1
)

echo Pylades — services herstarten…
uv run python scripts/pylades_services.py restart
if errorlevel 1 (
  echo Herstart mislukt.
  pause
  exit /b 1
)

for /f "delims=" %%p in ('uv run python -c "from shared.config import settings; print(settings.ui_port)"') do set UI_PORT=%%p
set "APP_URL=http://127.0.0.1:!UI_PORT!"
set "HEALTH_URL=!APP_URL!/_stcore/health"

echo Wachten tot Streamlit bereikbaar is…
set READY=0
for /L %%i in (1,1,60) do (
  curl -sf --max-time 2 "!HEALTH_URL!" >nul 2>&1
  if not errorlevel 1 (
    set READY=1
    goto streamlit_ready
  )
  powershell -NoProfile -Command "Start-Sleep -Milliseconds 500" >nul 2>&1
)
:streamlit_ready

if "!READY!"=="1" (
  echo Browser openen: !APP_URL!
  start "" "!APP_URL!"
) else (
  echo Streamlit reageert nog niet op !HEALTH_URL!
  echo Probeer handmatig: start !APP_URL!
)

echo.
echo Services draaien op de achtergrond (logs: logs\proxy.log, logs\streamlit.log^).
echo Stoppen: uv run python scripts/pylades_services.py stop
echo.
pause
