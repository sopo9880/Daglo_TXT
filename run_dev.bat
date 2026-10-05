@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
set "PLAYWRIGHT_BROWSERS_PATH=%cd%\browsers"
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto fail
".venv\Scripts\python.exe" daglo_collector.py
if errorlevel 1 goto fail
exit /b 0
:fail
echo Failed. Install Python 3.12 x64 and review the error above.
pause
exit /b 1
