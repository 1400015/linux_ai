# Windows launcher for the Linux AI Assistant.
# Usage: .\run.ps1 [-Ui auto|gtk|qt]  (default: auto: Qt on Windows, GTK on Linux)


param(
    [ValidateSet("auto", "gtk", "qt")]
    [string]$Ui = "auto"
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
