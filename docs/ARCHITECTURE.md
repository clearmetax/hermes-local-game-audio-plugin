# Architecture

```
Hermes Agent ── game-audio skill + 9 native tools (toolset game_audio)
   │  register(ctx): ctx.register_tool / register_skill / register_hook / register_command
   ▼
tools.py ── validation, folders/naming, analyze/process, game workflow, integration records
   │
pipeline.py ── prompt building → N takes → validate → analyse → master → re-analyse → CLAP → rank → publish
   ├── analysis.py / mastering.py / dsp.py   (pure-Python BS.1770 loudness, true peak, limiter, loops, tempo, FFT)
   ├── wav.py / codecs_io.py                 (RIFF + smpl loops, TPDF dither, FLAC decoder, ffmpeg Ogg)
   ├── semantic/                             (CLAP worker in its own venv; optional)
   └── audio_backend/                        (all local)
         agent_audio.py ── stdio MCP sidecar to Agent Audio (Stable Audio 3 Medium) or ctx.call_mcp
         comfyui.py     ── localhost ComfyUI API; patches exported workflows (Stable Audio Open, ACE-Step 1.x)
         procedural*.py ── placeholder SFX / chiptune music / ambience synth (opt-in, not AI)
game_adapters/ ── detect.py, godot.py (SFX + music/ambience edits), unity.py, unreal.py, others.py, scan.py
```

## Generation path

See [QUALITY.md](QUALITY.md) for the measurement and mastering details.

1. Validate the request (description ≤ 1000 chars, duration within settings).
2. Build the prompt for the kind (SFX/UI/ambience suffixes, or genre/mood/
   instruments/BPM/key/loop/stinger tags for music). Agent Audio rejects
   a separate negative prompt, so exclusions (`no music`, `no voice`, `no vocals`)
   are written into the prompt text itself; ComfyUI Stable
   Audio workflows also get a per-kind negative prompt.
3. Acquire the single generation slot (local inference is serial).
4. For each take: render into the plugin's `renders/` folder, validate the WAV
   (or decode FLAC/MP3), analyse it, master it (loops are built here), analyse
   again, and keep the encoded result in `candidates/<batch>/`.
5. Optionally score all takes with CLAP in one model load; rank; keep going
   while a `min_score` gate is unmet and attempts remain.
6. Publish the best take into the project with a temp file + hardlink (or
   `O_EXCL`), or encode Ogg via ffmpeg. An existing file is never replaced.
7. Clean up renders, append a JSONL manifest entry in plugin-data.

## Integration path (Godot 4)

`plan_script_edit` is a pure function over the script text. It adds a
`const SFX_X / MUSIC_X / AMB_X := preload("res://…")`, one helper per script
and mode, and one call at the start of the function or after an `anchor` line:

- one-shot SFX (`_hlga_play_sfx[_2d|_3d](stream, volume_db, pitch_variation)`):
  a fresh `AudioStreamPlayer[2D|3D]` parented to the current scene (so a death
  sound survives `queue_free()`), `SFX` bus if present, freed when finished;
- music / ambience (`_hlga_play_music|_hlga_play_ambience(stream, volume_db, fade_in)`):
  a persistent `HLGA_Music` / `HLGA_Ambience` child that loops (WAV loop
  points from the file, otherwise a forward loop over the whole file; Ogg/MP3
  `loop = true`), fades in, crossfades when a different track starts, and uses
  the `Music` / `Ambience` bus if present.

Indentation style and line endings are preserved. `apply` backs up the
original, writes a `.patch`, replaces the file atomically only if it is
unchanged since it was read, then runs static checks and — when Godot is
available — `--headless --import` and `--check-only --script`. `revert`
restores the backup only if the file still matches what the plugin wrote (or
with `force`). Validation levels: `static_checked`, `engine_parsed`, or a
failure; playback is reported as unverified unless a trigger test ran.

## Architecture decision record

**ADR-1: Native Hermes plugin.** Hermes's documented `plugin.yaml` +
`register(ctx)` contract gives tool registration, bundled skills, plugin-scoped
settings, profile-scoped data dirs, hooks and slash commands, and it is
checked by `hermes plugins doctor` / `validate`. A bare MCP registration would
expose only two generic tools and no game workflow.

**ADR-2: Reuse Agent Audio over MCP stdio instead of importing it.** Options
considered:

| Option | Verdict |
|---|---|
| Import `agent_audio` into Hermes's process | Rejected: drags `mcp`, `psutil` and Agent Audio's pins into Hermes's shared environment; private internals. |
| Shell out to `agent-audio generate` CLI | Viable, but returns plain text and re-implements nothing better than MCP; error mapping is weaker. |
| **Sidecar: launch its documented MCP server from its own venv** | **Chosen (default).** Same command Agent Audio registers for Codex/Claude/Cursor (`python -I -m agent_audio.mcp_server`), isolated environment, structured results, clean timeouts. One process per tool call — no daemons left running. |
| Hermes `ctx.call_mcp` to a configured server | Supported as `agent_audio_transport: hermes_mcp` for users who already configured it. Requires an explicit `mcp_allowlist` grant. |

The plugin speaks MCP with a ~200-line stdlib client so the plugin itself has
zero dependencies (`python_dependencies` is empty and `hermes plugins validate`
reports none).

**ADR-3: No silent fallbacks.** `auto` means an AI model (a configured ComfyUI
workflow for that kind, else Agent Audio). If it is missing,
tools return `BACKEND_NOT_CONFIGURED`/`BACKEND_NOT_READY` with setup steps.
The procedural synth is used only when the user or request selects it, and
every result carries `backend` and `is_ai_model`. There is no cloud path.

**ADR-4: Godot first, honest partial support elsewhere.** Godot scripts are
text and Godot can parse and import headlessly, so integration can be applied,
validated and reverted end-to-end. Unity (serialized inspector references,
`.meta` files) and Unreal (binary `.uasset` import) cannot be verified outside
their editors, so the plugin places assets and returns snippets/steps rather
than claiming an integration.

**ADR-5: State lives in Hermes plugin-data.** Manifests, render scratch and
integration backups go to `<HERMES_HOME>/plugin-data/hermes-local-game-audio/`
(resolved via `hermes_constants.get_hermes_home()` so profiles are respected),
never in the plugin install tree, which `hermes plugins update/remove` replace.

**ADR-6: One pipeline for every kind.** SFX, UI, ambience, music and stingers
share prompt → takes → analysis → mastering → ranking → publish, with per-kind
prompt suffixes, negative prompts, loudness targets and analysis rules. That
keeps quality behaviour and reporting consistent.

**ADR-7: ComfyUI by node type, not placeholders.** Users export the workflow
that already works for them. The plugin finds the prompt encoder by tracing
the sampler's positive/negative links (respecting paired outputs such as
`ConditioningStableAudio`), and patches seconds, seeds and ACE-Step music
fields. Model choice and quality settings stay the user's. Only localhost is
contacted by default.

**ADR-8: Pure-Python DSP.** Hermes ships without numpy, and adding packages
into Hermes's shared environment is exactly what ADR-2 avoids. The DSP is
written for O(n) passes and caches shared intermediate results; analysing a
45 s stereo track takes about 5 s and analysing + mastering it about 15 s
(about a second for a typical SFX). Correctness is pinned by
reference tests (BS.1770 tone, intersample peaks, limiter ceiling, tempo on
click tracks, bit-exact FLAC versus ffmpeg).

**ADR-9: Semantic scoring is a sidecar.** CLAP needs torch, so it runs in a
user-created venv over a JSON stdin/stdout protocol, offline after one model
download. When it's absent, ranking is technical only and results say so.

**ADR-10: Loops are built, not hoped for.** Prompts ask for loopable material,
but the seam is constructed: bar-snapped length, correlation-chosen end point,
equal-power crossfade of the overflow into the head (wrap continuous by
construction), and an embedded RIFF `smpl` loop (Godot 4 imports it as a
forward loop; verified).

## Verified facts about the upstream pieces (Oct 2026)

- Hermes: handlers are `handler(args, **kwargs) -> str`; `ctx.get_config` reads
  `plugins.entries.<id>.settings`; `ctx.register_skill(name, path)` exposes
  `skill_view("<plugin>:<name>")`; plugin skills are not auto-listed in the
  system prompt (hence the `pre_llm_call` hint); `ctx.call_mcp` is
  allowlist-gated and clamps timeouts to 600 s.
- Agent Audio v0.1: tools `audio_status` and `generate_audio(prompt, seconds,
  output_path, negative_prompt)`; `0 < seconds ≤ 380`; 540 s inference timeout;
  nonempty negative prompts rejected; outputs never overwritten; generation
  runs with `HF_HUB_OFFLINE=1`; MCP SDK pinned to `mcp>=1.30,<2`.
