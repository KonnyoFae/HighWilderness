[CmdletBinding()]
param([switch]$BuildOnly)
$ErrorActionPreference = 'Stop'
$tacticalRoot = Split-Path -Parent $PSScriptRoot
# Both entry points run the same desktop binary and share source/build validation.
& (Join-Path $PSScriptRoot 'Start-ShipEditors.ps1') -BuildOnly
if (-not $BuildOnly) {
    $tacticalExecutable = Join-Path $tacticalRoot 'apps\desktop\src-tauri\target\debug\high-wilderness-desktop.exe'
    Start-Process -FilePath $tacticalExecutable -ArgumentList '--tactical' -WorkingDirectory $tacticalRoot -WindowStyle Hidden
}
