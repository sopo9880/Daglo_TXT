@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto fail
)
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Python ".venv\Scripts\python.exe"
if errorlevel 1 goto fail
echo Installer ready in release
pause
exit /b 0
:fail
echo Build failed. Install Python 3.12 x64 and Inno Setup 6.7.3. See the error above.
pause
exit /b 1
