# Optional: install the CLAP prompt-match scorer in its own environment (~1.5 GB with the model).
# It ranks generated takes by how well they match the text. Hermes's own environment is not touched.
param([string]$Venv = "")
$ErrorActionPreference = "Stop"
$HomeDir = if ($env:HERMES_HOME) { $env:HERMES_HOME } else { Join-Path $env:USERPROFILE ".hermes" }
if (-not $Venv) { $Venv = Join-Path $HomeDir "plugin-data\hermes-local-game-audio\clap-venv" }
$Src = Split-Path -Parent $PSScriptRoot
$Py = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $Py -m venv $Venv
$VPy = Join-Path $Venv "Scripts\python.exe"
& $VPy -m pip install --upgrade pip
& $VPy -m pip install "torch>=2.6,<3"
& $VPy -m pip install "transformers>=4.45,<5" "numpy<3"
& $VPy -I (Join-Path $Src "semantic\clap_worker.py") --warmup laion/larger_clap_general
if ($LASTEXITCODE -ne 0) { throw "CLAP model download failed." }
hermes config set plugins.entries.hermes-local-game-audio.settings.clap_python "$VPy"
Write-Host "CLAP scorer ready: $VPy"
