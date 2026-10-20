# Configuration reference

Settings live in Hermes's `config.yaml` under
`plugins.entries.hermes-local-game-audio.settings`. They are read on every
tool call, so changes apply without restarting Hermes. Three ways to set them:

```bash
hermes config set plugins.entries.hermes-local-game-audio.settings.music_backend comfyui
```

- The Desktop app: **Settings → Plugins → Hermes Local Game Audio** (the form
  is generated from `plugin.yaml`).
- Editing `config.yaml` directly (example at the end of this page).

## All settings

### Backends and routing

| Key | Default | Description |
|---|---|---|
| `backend` | `auto` | Backend for all kinds: `auto`, `agent_audio`, `comfyui`, `procedural`. `auto` = the ComfyUI workflow configured for that kind if any; otherwise Agent Audio; if no Agent Audio checkout exists but some ComfyUI workflow is set, ComfyUI for every kind. It never silently falls back to `procedural`. |
| `music_backend` | `auto` | Overrides `backend` for music, stingers and ambience (for example ComfyUI ACE-Step for music and Agent Audio for SFX). `auto` = follow `backend`. |

### Agent Audio (Stable Audio 3 Medium)

| Key | Default | Description |
|---|---|---|
| `agent_audio_checkout` | auto-detect | Path to the `agent-audio` git checkout (the folder with `pyproject.toml` and `.venv`). If set, it is used as-is (an invalid path is reported, not replaced by auto-detection). When empty, auto-detected in `~/agent-audio`, `~/src|code|dev|projects|repos/agent-audio`, `~/Documents/GitHub/agent-audio`, `~/Desktop/agent-audio`, `~/OneDrive/Desktop/agent-audio`, `~/OneDrive/Documents/GitHub/agent-audio`, or next to the plugin folder. |
| `agent_audio_home` | Agent Audio default (`~/.agent-audio`) | `AGENT_AUDIO_HOME` passed to the server. Must match what you used when installing Agent Audio. |
| `agent_audio_transport` | `sidecar` | `sidecar`: the plugin starts Agent Audio's MCP server from its checkout for each tool call. `hermes_mcp`: use an `agent-audio` entry in Hermes's own `mcp_servers` through `ctx.call_mcp` (also needs `mcp_allowlist`, see below). |
| `agent_audio_mcp_server` | `agent-audio` | Name of that Hermes MCP server entry (only for `hermes_mcp`). |

### ComfyUI (Stable Audio Open, ACE-Step)

| Key | Default | Description |
|---|---|---|
| `comfyui_url` | `http://127.0.0.1:8188` | ComfyUI server address. |
| `comfyui_workflow_music` | – | API-format workflow JSON used for music, stingers and ambience (e.g. ACE-Step 1.5). |
| `comfyui_workflow_sfx` | – | API-format workflow JSON used for SFX and UI (e.g. Stable Audio Open). When ComfyUI handles a kind that has no workflow of its own (`backend: comfyui`, or `auto` with no Agent Audio checkout), the other workflow is used. |
| `comfyui_allow_remote` | `false` | Allow a non-localhost `comfyui_url`. Leave off unless you trust that machine. |

### Generation limits

| Key | Default | Description |
|---|---|---|
| `default_duration_seconds` | `2.0` | SFX length when a request doesn't specify one (ambience defaults to 20 s, stingers to 4 s). |
| `max_duration_seconds` | `30.0` | Upper bound for SFX, UI and ambience (hard cap 120). |
| `default_music_seconds` | `45.0` | Music length when not specified. |
| `max_music_seconds` | `180.0` | Upper bound for music (hard cap 380, Agent Audio's own limit). |
| `generation_timeout_seconds` | `600` | Per take (10–1800). Agent Audio stops inference at 540 s; Hermes caps `hermes_mcp` calls at 600 s. |
| `max_variations` | `4` | Upper bound for `generate_variations` `count` and per-sound `variations` (hard cap 8). |

### Quality

| Key | Default | Description |
|---|---|---|
| `default_candidates` | `2` | Takes generated per SFX/UI/ambience request; the best is kept. |
| `default_music_candidates` | `1` | Takes per music/stinger request (music is slow on CPU; raise it on a GPU). |
| `max_candidates` | `4` | Upper bound for `candidates` (hard cap 8); `max_attempts` may go up to twice this. |
| `clap_python` | – | Python executable of the separate CLAP environment (`scripts/setup_clap.*` sets it). Empty = semantic scoring off. |
| `clap_model` | `laion/larger_clap_general` | Hugging Face model id used by the CLAP scorer. |

### Output and files

| Key | Default | Description |
|---|---|---|
| `output_format` | `wav` | Default format: `wav` or `ogg` (Vorbis, needs ffmpeg; falls back to WAV with a note). |
| `ffmpeg_executable` | PATH lookup | ffmpeg for Ogg export and for decoding MP3/Opus/Ogg inputs. FLAC decodes without it. |
| `output_dir` | `<HERMES_HOME>/plugin-data/hermes-local-game-audio/library` | Where sounds go when no `project_path` is given (subfolders `sfx/`, `music/`, `ambience/`). |
| `allowed_project_roots` | `[]` (any) | Optional allow-list of folders. When set, the plugin only reads or writes projects (and loose files for `analyze_audio`/`process_audio`) inside them. |

### Game engine and assistant behaviour

| Key | Default | Description |
|---|---|---|
| `godot_executable` | auto-detect | Godot 4 binary for headless import + script parsing after integration. Auto-detected from `GODOT`/`GODOT_BIN`, PATH (`godot`, `godot4`, `godot-mono`…), `~/Downloads`, `~/Desktop`, `~/OneDrive/Desktop`, Scoop, `C:/Program Files/Godot*`, Steam, and `/Applications/Godot.app`. |
| `inject_skill_hint` | `true` | Adds a one-line pointer to the `game-audio` skill (once per session) when a message looks like game work. Turn off if you'd rather invoke it yourself. |

## Settings outside the plugin's own block

```yaml
plugins:
  entries:
    hermes-local-game-audio:
      mcp_allowlist: ["agent-audio"]    # only needed for agent_audio_transport: hermes_mcp
mcp_servers:
  agent-audio:                           # only needed for agent_audio_transport: hermes_mcp
    command: "C:/Users/you/agent-audio/.venv/Scripts/python.exe"
    args: ["-I", "-m", "agent_audio.mcp_server"]
```

## Environment variables

| Variable | Effect |
|---|---|
| `HLGA_<SETTING>` (e.g. `HLGA_BACKEND=procedural`, `HLGA_DEFAULT_CANDIDATES=3`) | Overrides any setting above; takes precedence over `config.yaml`. Lists use JSON (`["C:/Games"]`) or the OS path separator. |
| `HLGA_DATA_DIR` | Moves the plugin's data folder (normally `<HERMES_HOME>/plugin-data/hermes-local-game-audio`). |
| `HERMES_HOME` | Hermes's home; the data folder follows it (and the active profile). |
| `AGENT_AUDIO_CHECKOUT`, `AGENT_AUDIO_HOME` | Used when the corresponding settings are empty. |
| `GODOT`, `GODOT_BIN` | Godot executable fallback. |
| `FFMPEG_BINARY` | ffmpeg fallback. |
| `HLGA_CLAP_PYTHON` | CLAP Python fallback. |

Test-only variables are listed in [DEVELOPMENT.md](DEVELOPMENT.md#optional-real-world-tests).

## Data folder layout

`<HERMES_HOME>/plugin-data/hermes-local-game-audio/` (survives plugin updates
and uninstalls):

| Path | Contents |
|---|---|
| `library/{sfx,music,ambience}/` | Sounds generated without a `project_path` |
| `candidates/<timestamp>-<id>/take_NN.wav` | Every mastered take of every request (alternates you can swap in) |
| `renders/` | Temporary raw model output, deleted after each take |
| `manifests/<project-hash>.jsonl`, `manifests/library.jsonl` | One line per generated or prepared asset: time, tool, description, prompt, backend, path, quality score |
| `backups/<integration-id>/` | `record.json`, `change.patch` and the original file for each `integrate_game_audio` apply (used by revert) |
| `clap-venv/` | The optional CLAP environment, if created by `setup_clap` |

You can delete `candidates/` at any time to reclaim space.

## Example `config.yaml` block

```yaml
plugins:
  entries:
    hermes-local-game-audio:
      settings:
        backend: auto
        music_backend: comfyui
        comfyui_workflow_music: "C:/ComfyUI/user/default/workflows/ace_step_15_api.json"
        agent_audio_checkout: "C:/Users/you/agent-audio"
        default_candidates: 3
        default_music_candidates: 2
        clap_python: "C:/Users/you/.hermes/plugin-data/hermes-local-game-audio/clap-venv/Scripts/python.exe"
        output_format: ogg
        godot_executable: "C:/Godot/Godot_v4.3-stable_win64.exe"
        allowed_project_roots: ["C:/Games"]
```
