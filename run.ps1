# Windows launcher for the Linux AI Assistant Qt track (phase 4c).
# Usage: .\run.ps1 [-Ui gtk|qt]

param(
    [ValidateSet("gtk", "qt")]
    [string]$Ui = "qt"
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectDir

# Prefer the project venv when present, matching run.sh.
if (Test-Path (Join-Path $ProjectDir "venv\Scripts\python.exe")) {
    & (Join-Path $ProjectDir "venv\Scripts\python.exe") -m src.app --ui $Ui
} else {
    & python -m src.app --ui $Ui
}
exit $LASTEXITCODE
