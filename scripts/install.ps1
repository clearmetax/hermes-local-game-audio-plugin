# Install this plugin into Hermes on Windows (PowerShell).
#   .\scripts\install.ps1           copy into %HERMES_HOME%\plugins (default ~\.hermes) and enable
#   .\scripts\install.ps1 -Update   replace an existing copy (old copy kept as a backup)
param([switch]$Update)
$ErrorActionPreference = "Stop"
$Name = "hermes-local-game-audio"
$Src = Split-Path -Parent $PSScriptRoot
$HomeDir = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:USERPROFILE ".hermes" }
$Dest = Join-Path $HomeDir "plugins\$Name"

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { throw "hermes CLI not found on PATH. Install Hermes Agent first." }
hermes plugins validate "$Src"
if ($LASTEXITCODE -ne 0) { throw "Plugin validation failed." }

if (Test-Path $Dest) {
    if (-not $Update) { throw "$Dest already exists. Re-run with -Update to replace it (a backup is kept)." }
    $Backup = Join-Path $HomeDir ("plugin-backups\$Name-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Force -Path (Split-Path $Backup) | Out-Null
    Move-Item $Dest $Backup
    Write-Host "Previous copy moved to $Backup"
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Items = "plugin.yaml","__init__.py","schemas.py","tools.py","settings.py","errors.py","safety.py","wav.py","dsp.py","analysis.py","mastering.py","pipeline.py","codecs_io.py","audio_backend","game_adapters","semantic","skills","LICENSE","README.md"
foreach ($i in $Items) { Copy-Item -Recurse -Force (Join-Path $Src $i) $Dest }
Get-ChildItem $Dest -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
hermes plugins doctor "$Dest" --ci
if ($LASTEXITCODE -ne 0) { throw "Plugin doctor reported an error." }
hermes plugins enable $Name
Write-Host "Installed to $Dest. Start a new Hermes session, then run /game-audio deep"
