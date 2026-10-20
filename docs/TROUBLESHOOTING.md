# Troubleshooting

Start with `/game-audio deep` (or ask Hermes to run `audio_status` with
`deep_check: true`). It shows the routing, each backend's problems and the
exact setup steps.

## Error codes

Every failed tool call returns `{"success": false, "error": {"code", "message", "hint"?, "details"?}}`:

| Code | Meaning | Fix |
|---|---|---|
| `INVALID_INPUT` | A parameter is missing, out of range or malformed (the message names it: duration, bpm, key, crossfade vs loop length, sound names, …) | Correct that parameter. Limits are listed in [CONFIGURATION.md](CONFIGURATION.md#generation-limits). |
| `PROJECT_NOT_FOUND` | `project_path` doesn't exist or isn't a folder | Pass the game's root folder (the one with `project.godot`, `Assets/`, `*.uproject`, …). |
| `BACKEND_NOT_CONFIGURED` | No AI backend for this kind: Agent Audio checkout not found, no ComfyUI workflow, or `hermes_mcp` without an allowlist grant | Install Agent Audio and/or configure a ComfyUI workflow ([INSTALL.md](INSTALL.md)); for `hermes_mcp` add the server to `mcp_allowlist`. |
| `BACKEND_NOT_READY` | Backend found but can't run: Agent Audio's `.venv`/runtime/models missing, or ComfyUI not reachable | Agent Audio: `uv sync --frozen`, then `install/bootstrap.py --runtime-only`, and accept model terms. ComfyUI: start it and check `comfyui_url`. |
| `BACKEND_ERROR` | Server failed to start, crashed, rejected the workflow, or returned an error | Read `error.message`, `details.server_log_tail` or `details.node_errors`; check Agent Audio's logs in `AGENT_AUDIO_HOME/logs` or ComfyUI's console. |
| `GENERATION_TIMEOUT` | Inference exceeded the timeout | Use shorter sounds (≤ 3 s); CPU inference is slow. Raise `generation_timeout_seconds` (Agent Audio itself stops at 540 s). |
| `INVALID_OUTPUT` | Backend produced an unusable WAV | Retry once; if repeatable, report to Agent Audio with the backend details. |
| `FILE_EXISTS` | Target name taken and `on_conflict: error` | Choose another filename. |
| `PATH_REJECTED` | Path escapes the project, crosses a link, or is outside `allowed_project_roots` | Pass the real project folder and project-relative paths. |
| `OUT_OF_SPACE` | < 200 MB free on the target volume | Free space or change `output_dir`. |
| `INTEGRATION_FAILED` | Function not found (the error lists available ones), static/one-line function, or non-Node script | Pick the right script/function, or edit manually. |
| `UNSUPPORTED_ENGINE` | Godot 3 project for automatic edits | Hook the asset manually, or upgrade to Godot 4. |
| `CONFLICT` | File changed between read and write, or after an integration (revert) | Review the file; revert with `force: true` only if you agree. |
| `INTERNAL_ERROR` | An unexpected bug in the plugin (the message has the exception type) | Retry once. If it repeats, check `~/.hermes/logs/agent.log` and report it with the tool name and arguments (no private content). |

## Music, ComfyUI and quality

**Music took forever or timed out.** CPU inference scales with length. Use
30–45 s loops; a seamless loop sounds longer than it is. With a GPU, use the
ComfyUI ACE-Step backend.

**"cannot force a tempo" in notes.** Agent Audio (Stable Audio 3 Medium) only
takes text, so BPM and key are requested in the prompt and then measured. For
exact tempo and key use ComfyUI with ACE-Step 1.5.

**The loop has an audible bump.** Check `loop.seam.score`. Regenerate with
`candidates: 2`, use a longer `crossfade_seconds` (1.5–2 s for pads and
ambience), give `bpm` so the loop is bar-aligned, or run `process_audio` with
`loop: true` on a longer take.

**ComfyUI: "not in ComfyUI API format".** Re-export with *Workflow → Export (API)*.
**"missing node types"**: install the custom nodes the workflow uses, or start
from the core templates. **"rejected the workflow … ckpt_name"**: the model
file named in the workflow isn't in your ComfyUI models folder.
**"is not on this computer"**: the URL isn't localhost; set
`comfyui_allow_remote: true` only for a host you trust.

**CLAP scoring is off.** Run `scripts/setup_clap.*`, or set `clap_python` to a
Python that has torch ≥ 2.6 and transformers. The first run must download the
model (the setup script does this); after that scoring is offline.

**Quality verdict "poor".** Read `quality.issues`. Fixable ones (level, late
start, DC, abrupt end) are fixed by mastering automatically. The rest
(clipping baked into the take, phase problems, gaps, wrong tempo, noisy
spectrum) need a new take: more `candidates`, a `min_score`, or a more concrete
description.

**I want Ogg files.** Install ffmpeg or set `ffmpeg_executable`, then pass
`output_format: "ogg"`. Without ffmpeg the plugin saves WAV and says so
(`format_note` in the result, for both generation and `process_audio`).

## Common situations

**The tools don't show up.** Start a new Hermes session after enabling. Check
`hermes plugins list` (enabled?) and `hermes tools list` (`game_audio`
enabled?). Run `HERMES_PLUGINS_DEBUG=1 hermes plugins list` for discovery logs.
For the gateway, `hermes gateway restart`.

**Hermes generates sounds but never uses the skill.** Plugin skills aren't in
the system prompt; the plugin adds a one-line hint on game-looking messages.
You can also say "use the game-audio skill".

**`ready=True` but generation fails.** `runtime_ready` only checks files and
model headers. Read the error; on Windows look for out-of-memory in Agent
Audio's log. Try 1–2 s first.

**The sound exists but I don't hear it in Godot.** Generation and integration
are separate. Make sure `integrate_game_audio` reported `applied`, open the
project once in the editor (or let the plugin run `--import`) so the WAV is
imported, check the `SFX`/Master bus volume, and trigger the event in-game.

**Engine check says "Godot executable not found".** Set `godot_executable` to
your Godot 4 binary (e.g. `C:/Godot/Godot_v4.3-stable_win64.exe`). Use the
non-console build; either works headless.

**I want the raw model output.** Pass `post_process: {"enabled": false}`.

**Uninstalling left disk space used.** Uninstall keeps your data on purpose.
Delete `<HERMES_HOME>/plugin-data/hermes-local-game-audio/` (library,
candidates, manifests, backups and the CLAP venv) and Agent Audio's
`AGENT_AUDIO_HOME` yourself if you want the space back.

**Windows: "running scripts is disabled on this system".** Run
`powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1` (or
`setup_clap.ps1`), or allow local scripts with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Undo an integration.** Ask Hermes to revert it, or call
`integrate_game_audio` with `action: "revert"` and the `integration_id` from
the apply result. Backups are in `<HERMES_HOME>/plugin-data/hermes-local-game-audio/backups/<id>/`.
