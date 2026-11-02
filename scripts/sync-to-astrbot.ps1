# Sync this repo into the AstrBot plugins directory (dev helper).
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/sync-to-astrbot.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/sync-to-astrbot.ps1 -Reload

param(
    [string]$AstrBotData = "",
    [switch]$Reload
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($AstrBotData)) {
    $AstrBotData = Join-Path $env:USERPROFILE ".astrbot\data"
}

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pluginsDir = Join-Path $AstrBotData "plugins"
$target = Join-Path $pluginsDir "astrbot_plugin_scinderbook"

if (-not (Test-Path $pluginsDir)) {
    throw "AstrBot plugin directory not found: $pluginsDir"
}

Write-Host "source: $root"
Write-Host "target: $target"

# /MIR mirrors the tree; skip VCS metadata, caches and local test artifacts.
& robocopy $root $target /MIR /NFL /NDL /NJH /NJS /NP /XD ".git" "__pycache__" "data" "node_modules" | Out-Null

if ($LASTEXITCODE -ge 8) {
    throw "robocopy failed with exit code $LASTEXITCODE"
}

Write-Host "sync done."
if ($Reload) {
    Write-Host "next: AstrBot WebUI -> plugins -> astrbot_plugin_scinderbook -> reload"
} else {
    Write-Host "AstrBot usually hot-reloads changed .py files; if not, click reload in the WebUI."
}
