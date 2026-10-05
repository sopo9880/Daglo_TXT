param([string]$Python = 'python', [string]$ISCC = '', [switch]$SkipDependencies)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
function Check-Exit { if ($LASTEXITCODE -ne 0) { throw "Build step failed ($LASTEXITCODE)" } }
if (-not $SkipDependencies) {
    & $Python -m pip install -r requirements-build.txt
    Check-Exit
}
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot 'browsers'
& $Python -m playwright install chromium
Check-Exit
& $Python -m unittest discover -s tests -v
Check-Exit
& $Python -m PyInstaller --noconfirm --clean DagloTranscriptCollector.spec
Check-Exit
$testData = Join-Path $PSScriptRoot 'build/smoke-data'
$env:DAGLO_DATA_DIR = $testData
$smoke = Start-Process -FilePath (Join-Path $PSScriptRoot 'dist/DagloTXT/DagloTXT.exe') -ArgumentList '--self-test','--browser-test' -PassThru -WindowStyle Hidden
if (-not $smoke.WaitForExit(120000)) { $smoke.Kill(); throw 'Packaged smoke test timed out' }
if ($smoke.ExitCode -ne 0 -or -not (Test-Path "$testData/self-test.json")) { throw 'Packaged smoke test failed' }
Remove-Item Env:DAGLO_DATA_DIR
$version = & $Python -c 'from version import APP_VERSION; print(APP_VERSION)'
if (-not $ISCC) { $ISCC = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' }
& $ISCC "/DAppVersion=$version" installer.iss
Check-Exit
New-Item -ItemType Directory -Path release -Force | Out-Null
Compress-Archive -Path dist/DagloTXT -DestinationPath "release/DagloTXT-Portable-$version.zip" -Force
& $Python tools/make_checksums.py
Check-Exit
Write-Host "Installer and portable archive ready in $PSScriptRoot/release"
