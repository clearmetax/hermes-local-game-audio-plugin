# Install, configure, update, uninstall

## 1. Requirements

| Piece | Needed for | Notes |
|---|---|---|
| Hermes Agent | everything | Any build with native `plugin.yaml` + `register(ctx)` plugins (checked with `hermes plugins doctor`). |
| Python 3.11+ | the plugin | Uses Hermes's own interpreter. **No extra packages** are installed into Hermes. |
| [Agent Audio](https://github.com/AIEGOBOT/agent-audio) (this and/or ComfyUI) | AI sound and music generation (CPU is fine) | Git, Python 3.11+, `uv`. Its runtime uses Python 3.12 in its own virtualenv. |
| Disk / RAM | AI generation | Agent Audio's guidance: **25 GB+ free disk and a 32 GB-class RAM system** on Windows CPU; models are ~7.36 GB. These are planning figures, not verified minimums. |
| ComfyUI (this and/or Agent Audio) | AI SFX/music with seeds, steps, negative prompts; ACE-Step BPM/key/lyrics | Running locally (default `http://127.0.0.1:8188`). See [examples/comfyui](../examples/comfyui/README.md). |
| CLAP scorer (optional) | ranking takes by prompt match | Separate venv with torch ≥ 2.6 + transformers; `scripts/setup_clap.*` (~1.5 GB). |
| ffmpeg (optional) | Ogg export, decoding MP3/Opus | FLAC from ComfyUI decodes without it. |
| Godot 4 (optional) | headless validation | Auto-detected on PATH or common install folders; or set `godot_executable`. |

You need **at least one** of Agent Audio or ComfyUI for AI audio. The
procedural placeholder synth (SFX, simple music, ambience) needs nothing extra.

## 2. Install the plugin

From this folder:

```powershell
.\scripts\install.ps1            # Windows
```
```bash
./scripts/install.sh             # macOS / Linux / WSL
```

The script runs `hermes plugins validate`, copies the runtime files into
`<HERMES_HOME>/plugins/hermes-local-game-audio` (default `~/.hermes`), runs
`hermes plugins doctor --ci` on the copy and `hermes plugins enable`. It refuses
to replace an existing copy unless you pass `-Update` / `--update`, and then
moves the old copy to `<HERMES_HOME>/plugin-backups/` instead of deleting it.

Manual equivalent: copy the folder to `~/.hermes/plugins/hermes-local-game-audio`
and run `hermes plugins enable hermes-local-game-audio`. If you use the Hermes
gateway (Telegram, Discord…), run `hermes gateway restart` afterwards.

Check it in a **new** session:

```text
/game-audio            quick status
/game-audio deep       also starts the model server and checks readiness
```

`hermes tools list` should show `✓ enabled  game_audio`.

## 3. Install Agent Audio (the local AI model) — one time

```bash
git clone https://github.com/AIEGOBOT/agent-audio.git ~/agent-audio
cd ~/agent-audio
uv sync --frozen
uv run --frozen python install/bootstrap.py --doctor
uv run --frozen python install/bootstrap.py --runtime-only     # downloads the runtime + ~7.4 GB of models
```

- You must review and accept the Stable Audio 3 Medium (Stability AI Community
  License) and Gemma terms yourself, and complete any Hugging Face sign-in the
  installer asks for. This plugin never accepts terms for you.
- You do **not** need Agent Audio's `--register-only` step for Hermes; this
  plugin launches its MCP server itself.
- Data goes to `~/.agent-audio` unless you set `AGENT_AUDIO_HOME` before
  installing (then set the plugin's `agent_audio_home` setting to the same path).

The plugin finds the checkout automatically in `~/agent-audio`,
`~/src|code|dev|projects|repos/agent-audio`, `~/Documents/GitHub/agent-audio`,
`~/Desktop/agent-audio`, `~/OneDrive/Desktop/agent-audio`, or next to this
plugin. Anywhere else, set it:

```bash
hermes config set plugins.entries.hermes-local-game-audio.settings.agent_audio_checkout "C:/Users/you/tools/agent-audio"
```

Run `/game-audio deep` again: `ready=True` means the runtime files and model
headers are present. The first real generation is the actual proof — try
*"generate a 2 second heavy metallic impact"*.

## 3b. Or use ComfyUI (recommended for music with a GPU)

Follow [examples/comfyui/README.md](../examples/comfyui/README.md): run an
ACE-Step 1.5 (music) and/or Stable Audio Open (SFX) workflow once in ComfyUI,
export it with *Export (API)*, then:

```bash
hermes config set plugins.entries.hermes-local-game-audio.settings.comfyui_workflow_music "C:/path/ace_step_api.json"
hermes config set plugins.entries.hermes-local-game-audio.settings.comfyui_workflow_sfx "C:/path/stable_audio_open_api.json"
```

With `backend: auto`, each kind uses its ComfyUI workflow when one is set and
Agent Audio otherwise (with no Agent Audio checkout, a single configured
workflow serves every kind). `/game-audio deep` shows the resulting routing.

## 3c. Optional quality add-ons

- **CLAP prompt-match scoring:** run `.\scripts\setup_clap.ps1` (or
  `./scripts/setup_clap.sh`). It creates its own venv, downloads the model
  once and sets `clap_python`. See [QUALITY.md](QUALITY.md).
- **ffmpeg** on PATH (or `ffmpeg_executable`) enables `output_format: "ogg"`.

### Alternative: reuse a Hermes MCP server entry

If you already registered Agent Audio as a Hermes MCP server, you can route
through Hermes's own MCP client instead of the plugin's sidecar:

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  agent-audio:
    command: "C:/Users/you/agent-audio/.venv/Scripts/python.exe"
    args: ["-I", "-m", "agent_audio.mcp_server"]
plugins:
  entries:
    hermes-local-game-audio:
      mcp_allowlist: ["agent-audio"]
      settings:
        agent_audio_transport: hermes_mcp
```

Hermes caps plugin MCP calls at 600 s; Agent Audio stops inference at 540 s.

## 4. Settings

Every setting, its default and the environment-variable overrides are in
[CONFIGURATION.md](CONFIGURATION.md). The ones most people touch:

```bash
# where Agent Audio lives (if not auto-detected)
hermes config set plugins.entries.hermes-local-game-audio.settings.agent_audio_checkout "C:/Users/you/agent-audio"
# ComfyUI workflows (exported with Workflow > Export (API))
hermes config set plugins.entries.hermes-local-game-audio.settings.comfyui_workflow_music "C:/path/ace_step_api.json"
# more takes per request = better odds of a great one (slower)
hermes config set plugins.entries.hermes-local-game-audio.settings.default_candidates 3
# Godot for headless validation (if not auto-detected)
hermes config set plugins.entries.hermes-local-game-audio.settings.godot_executable "C:/Godot/Godot_v4.3-stable_win64.exe"
```

## 5. Where things are stored

| What | Where |
|---|---|
| Plugin code | `<HERMES_HOME>/plugins/hermes-local-game-audio/` |
| Library sounds, alternate takes (`candidates/`), manifests, integration backups, CLAP venv | `<HERMES_HOME>/plugin-data/hermes-local-game-audio/` (survives plugin updates) |
| Project sounds | inside each project (e.g. `res://audio/sfx/`) |
| Model runtime + weights | Agent Audio's `AGENT_AUDIO_HOME` (default `~/.agent-audio`) |

## 6. Update

Pull or copy the new version, then `.\scripts\install.ps1 -Update`
(or `./scripts/install.sh --update`). Your plugin-data folder is untouched.
Update Agent Audio separately following its README.

## 7. Uninstall

```bash
hermes plugins disable hermes-local-game-audio
hermes plugins remove hermes-local-game-audio      # or delete ~/.hermes/plugins/hermes-local-game-audio
```

This removes only the plugin code. Generated sounds in your projects, the
plugin-data folder (library, candidates, manifests, backups, CLAP venv) and
Agent Audio's models are kept. Delete
`<HERMES_HOME>/plugin-data/hermes-local-game-audio` and Agent Audio's data
folder yourself if you want that space back. Integrations already applied to
your game scripts stay in place; revert them first (ask Hermes, or call
`integrate_game_audio` with `action: "revert"`) if you want them gone.
