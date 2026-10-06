# Windows installer helpers for the Linux AI Assistant Qt track (phase 4c).
# Usage:
#   .\scripts\install.ps1 -EnableAutostart     # add the Registry Run value
#   .\scripts\install.ps1 -DisableAutostart    # remove the Registry Run value
#   .\scripts\install.ps1 -RemoveOnly          # (default) show status only

param(
    [switch]$EnableAutostart,
    [switch]$DisableAutostart,
    [switch]$RemoveOnly
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectDir

$pythonExe = (Get-Command python).Source
$command = '"{0}" -m src.app --ui qt' -f $pythonExe

if ($EnableAutostart) {
    & $pythonExe -c "from src.windows_autostart import apply_autostart, autostart_command, windows_backends; backends = windows_backends(); print(apply_autostart(True, autostart_command(r'$pythonExe', r'$ProjectDir'), *backends))" 2>$null
    if ($LASTEXITCODE -ne 0) {
        # Fall back to direct Registry writes when the package is not importable.
        $key = "Software\Microsoft\Windows\CurrentVersion\Run"
        New-ItemProperty -Path "HKCU:\$key" -Name "LinuxAIAssistant" -Value $command -PropertyType String -Force | Out-Null
        Write-Host "Autostart enabled (direct Registry write)."
    }
} elseif ($DisableAutostart) {
    $key = "Software\Microsoft\Windows\CurrentVersion\Run"
    if (Get-ItemProperty -Path "HKCU:\$key" -Name "LinuxAIAssistant" -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path "HKCU:\$key" -Name "LinuxAIAssistant"
        Write-Host "Autostart disabled."
    } else {
        Write-Host "Autostart was not enabled."
    }
} else {
    $key = "Software\Microsoft\Windows\CurrentVersion\Run"
    $value = (Get-ItemProperty -Path "HKCU:\$key" -Name "LinuxAIAssistant" -ErrorAction SilentlyContinue).LinuxAIAssistant
    if ($value) {
        Write-Host "Autostart is enabled: $value"
    } else {
        Write-Host "Autostart is disabled."
    }
}
