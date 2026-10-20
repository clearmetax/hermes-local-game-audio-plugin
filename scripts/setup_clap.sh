#!/usr/bin/env bash
# Optional: install the CLAP prompt-match scorer in its own environment (~1.5 GB with the model).
# It ranks generated takes by how well they match the text. Hermes's own environment is not touched.
set -euo pipefail
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
VENV="${1:-$HOME_DIR/plugin-data/hermes-local-game-audio/clap-venv}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
if [[ "$(uname)" == "Linux" ]]; then
  "$VENV/bin/python" -m pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.6,<3"
else
  "$VENV/bin/python" -m pip install "torch>=2.6,<3"
fi
"$VENV/bin/python" -m pip install "transformers>=4.45,<5" "numpy<3"
"$VENV/bin/python" -I "$SRC/semantic/clap_worker.py" --warmup laion/larger_clap_general
hermes config set plugins.entries.hermes-local-game-audio.settings.clap_python "$VENV/bin/python" || \
  echo "Set clap_python to: $VENV/bin/python"
echo "CLAP scorer ready: $VENV/bin/python"
