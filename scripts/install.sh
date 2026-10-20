#!/usr/bin/env bash
# Install this plugin into Hermes (macOS / Linux / WSL).
#   ./scripts/install.sh            copy into $HERMES_HOME/plugins and enable
#   ./scripts/install.sh --update   replace an existing copy (old copy kept as a backup)
set -euo pipefail
NAME="hermes-local-game-audio"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HOME_DIR/plugins/$NAME"
UPDATE=0
[[ "${1:-}" == "--update" ]] && UPDATE=1

command -v hermes >/dev/null || { echo "hermes CLI not found on PATH. Install Hermes Agent first."; exit 1; }
hermes plugins validate "$SRC"

if [[ -e "$DEST" ]]; then
  if [[ $UPDATE -ne 1 ]]; then
    echo "$DEST already exists. Re-run with --update to replace it (a backup is kept)."; exit 1
  fi
  BACKUP="$HOME_DIR/plugin-backups/$NAME-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$(dirname "$BACKUP")"
  mv "$DEST" "$BACKUP"
  echo "Previous copy moved to $BACKUP"
fi
mkdir -p "$DEST"
# Runtime files only; tests, examples and docs stay in the checkout.
for item in plugin.yaml __init__.py schemas.py tools.py settings.py errors.py safety.py wav.py dsp.py analysis.py mastering.py pipeline.py codecs_io.py audio_backend game_adapters semantic skills LICENSE README.md; do
  cp -R "$SRC/$item" "$DEST/"
done
find "$DEST" -name "__pycache__" -type d -prune -exec rm -rf {} +
hermes plugins doctor "$DEST" --ci
hermes plugins enable "$NAME"
echo "Installed to $DEST. Start a new Hermes session, then run /game-audio deep"
