# Tool reference

The plugin registers nine tools in the `game_audio` toolset. Hermes calls them
for you; you normally just ask in plain language (see [USAGE.md](USAGE.md)).
This page covers every parameter and the shape of every result, for reviewing
what Hermes did or for calling tools directly.

> The parameter tables are generated from `schemas.py` by
> `python scripts/gen_tool_docs.py`, and a test fails if they drift. Edit text
> outside the `BEGIN`/`END` markers by hand.

## Conventions shared by all tools

- **`post_process` (alias `mastering`)** accepts `true`/`false` as a shorthand
  for enabled/disabled, plus the fields in the tables below. Older field names
  still work: `normalize_peak_db` (peak target in dB, or `null` = no
  normalization) and `fade_out_ms`.

- **Results are JSON.** Success has `"success": true`. Failure is always
  `{"success": false, "error": {"code", "message", "hint"?, "details"?}}`. Codes
  are listed in [TROUBLESHOOTING.md](TROUBLESHOOTING.md#error-codes). Tools never
  raise into Hermes.
- **Project paths.** `project_path` is the game's root folder (absolute or
  relative to Hermes's working directory). File arguments inside a project
  accept `res://…` (Godot), project-relative (`audio/sfx/hit.wav`, either slash
  direction), or absolute paths that are inside the project. Anything that
  escapes the project, crosses a symlink/junction, or falls outside
  `allowed_project_roots` is rejected with `PATH_REJECTED`.
- **Never overwrites.** New files get a unique name (`name_02.wav`) unless you
  ask for `on_conflict: "error"`. `process_audio` always writes a new file.
- **Kinds.** `sfx`, `ui`, `ambience`, `music`, `stinger`. The kind picks the
  prompt style, the mastering targets, the analysis rules and the folder.
- **Backends.** `auto` (default), `agent_audio`, `comfyui`, `procedural`.
  `auto` uses the ComfyUI workflow configured for that kind; otherwise Agent
  Audio; and if no Agent Audio checkout exists but some ComfyUI workflow is
  configured, ComfyUI for every kind. It never picks the procedural
  placeholder synth.
- **Durations.** SFX/UI/ambience: 0.1 s up to `max_duration_seconds` (default
  30, hard cap 120). Music/stingers: 1 s up to `max_music_seconds` (default
  180, hard cap 380).

---

## `audio_status`

Readiness and setup information. Call it first in a session, with
`deep_check: true` once, to actually contact Agent Audio and ComfyUI.

<!-- BEGIN:audio_status -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `deep_check` | boolean |  | Start the backend and ask it for readiness. Default false. |
| `project_path` | string |  | Optional game project folder to report engine detection for. |
<!-- END:audio_status -->

**Returns** `routing` (`sfx`/`music` → which backend `auto` resolves to);
`backends.{agent_audio,comfyui,procedural}` each with `configured`, `ready`
(`false` until a deep check confirms it; always `true` for procedural), `problems`, `setup_steps`, `details`
(checkout, server command, ComfyUI workflows and devices, upstream status) and
`capabilities` per kind (`bpm`, `key`, `seed`, `lyrics`, `negative_prompt`,
`max_seconds`); `generation_ready` per kind (`null` when not deep-checked); `quality_tools` (CLAP configured?,
ffmpeg path, default candidates); `limits`; `paths` (`data_dir`,
`library_dir`); `machine` (OS, CPU count, RAM, free disk, Agent Audio
guidance, warnings); `godot_executable`; and with `project_path`, `project`
(engine detection and default SFX/music folders).

---

## `generate_sound`

One sound effect, UI sound or ambience bed. Generates `candidates` takes,
validates, analyses, masters and ranks them, and saves the best.

<!-- BEGIN:generate_sound -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `description` | string | yes | What the sound should be (max 1000 characters). |
| `kind` | `sfx` \| `ui` \| `ambience` |  | sfx (default), ui (short interface sounds), ambience (background bed, looped by default). |
| `loop` | boolean |  | Build a seamless loop (ambience). |
| `duration_seconds` | number |  | Length in seconds. Default from settings (2 s). |
| `style` | string |  | Optional aesthetic, e.g. 'retro 8-bit', 'realistic', 'cartoon', 'dark fantasy', 'sci-fi'. |
| `filename` | string |  | Desired file name (sanitized; .wav added). Default derived from the description. |
| `project_path` | string |  | Game project root. Omit to save to the plugin's sound library. |
| `subdir` | string |  | Project-relative folder. Default: the project's existing audio folder or the engine convention. |
| `on_conflict` | `rename` \| `error` |  | If the file exists: add a numeric suffix (default) or fail. Never overwrites. |
| `post_process` | object |  | Mastering overrides. Defaults per kind: sfx peak -1 dBTP; ui -20 LUFS; stinger/music -16 LUFS (-1 dBTP); ambience -24 LUFS (-3 dBTP); DC removal, silence trim, de-click fades (never on loops), lookahead limiting, 16-bit TPDF dither. Pass {"enabled": false} for the raw model output. |
| `post_process.enabled` | boolean | | false = skip mastering and keep the raw model output. |
| `post_process.target_lufs` | number | | Integrated loudness target, e.g. -16. |
| `post_process.peak_dbtp` | number | | Peak-normalize to this true-peak level instead of loudness. |
| `post_process.ceiling_dbtp` | number | | True-peak ceiling enforced by the limiter. |
| `post_process.trim_silence` | boolean | | Trim leading (and, for one-shots, trailing) silence. Default true. |
| `post_process.fade_out_seconds` | number | | Override the end fade (one-shots only; loops are never faded). |
| `post_process.mono` | boolean | | Downmix to mono (good for positional 2D/3D sounds). |
| `post_process.normalize` | boolean | | false = keep the level as generated (no loudness/peak normalization); limiting and fades still apply. |
| `post_process.remove_dc` | boolean | | Remove DC offset (default true). |
| `backend` | `auto` \| `agent_audio` \| `comfyui` \| `procedural` |  | Override the configured backend for this call. agent_audio = Stable Audio 3 Medium; comfyui = your local ComfyUI workflow (Stable Audio Open / ACE-Step: exact BPM, key, seed, lyrics). 'procedural' is a built-in synthesizer for quick placeholders (not AI); only use it when the user asks for placeholders or the AI backend is unavailable and the user agrees. |
| `seed` | integer |  | Seed for reproducible output (procedural and ComfyUI backends). |
| `candidates` | integer |  | Generate this many takes and keep the best-scoring one (default from settings: 2 for SFX, 1 for music). |
| `min_score` | number |  | Quality gate 0-100: keep generating (up to max_attempts) until a take reaches it. |
| `max_attempts` | integer |  | Upper bound on takes when min_score is used. |
| `keep_alternates` | boolean |  | Also save the non-chosen takes into the project as name_altN (default: kept in plugin data only). |
| `output_format` | `wav` \| `ogg` |  | ogg (Vorbis, smaller; needs ffmpeg) is recommended for long music in shipped games. |
<!-- END:generate_sound -->

**Example**

```json
{"project_path": "C:/Games/MyGodotGame", "description": "heavy steel sword striking a wooden shield, sharp transient, short metallic ring",
 "duration_seconds": 0.8, "style": "dark fantasy", "candidates": 3}
```

**Returns** (same shape for `generate_music`):

| Field | Meaning |
|---|---|
| `path`, `project_path`, `engine_ref` | Saved file: absolute, project-relative, engine path (`res://…` in Godot) |
| `format`, `audio` | `wav`/`ogg`; sample rate, channels, bits, frames, duration, peak, size |
| `embedded_loop` | `true` when the WAV carries `smpl` loop points |
| `kind`, `backend`, `is_ai_model` | What was made and by which backend (`is_ai_model: false` = placeholder synth) |
| `prompt_used` | The exact prompt sent to the model |
| `requested_seconds`, `generated_seconds`, `generation_seconds` | Requested length, length asked of the model (loops add a tail), wall time of the chosen take |
| `quality.score`, `quality.verdict` | 0–100 and `good` (≥ 85) / `acceptable` (≥ 65) / `poor`, after mastering |
| `quality.ranking_score`, `quality.semantic` | Score used to pick the take (technical, or 55% technical + 45% CLAP); CLAP details when enabled |
| `quality.issues`, `quality.issues_fixed_by_mastering` | Remaining problems (code, severity, message, fixable flag); problems mastering removed |
| `quality.metrics` | Every measurement (see [QUALITY.md](QUALITY.md)) |
| `mastering` | Human-readable list of processing steps, ending with the export step; loudness-targeted kinds (ui, music, stinger, ambience) include a `final loudness` step |
| `loop` | For loops: `loop_seconds`, `crossfade_seconds`, `bars`, `bpm_grid`, `phase_correlation`, `crossfade_match`, `seam.score` |
| `candidates` | `generated`, `failed`, `chosen_take`, per-take `scores`, `alternates` (files kept in plugin-data, or published with `keep_alternates`), `folder` |
| `quality_gate` | (`generate_music` with `min_score`) whether the bar was reached |
| `failures` | Takes that failed and why (the request still succeeds if one take worked) |
| `backend_capabilities`, `notes` | What the backend could enforce, and plain-language caveats (e.g. tempo requested but not enforceable) |
| `engine`, `manifest`, `note` | Detected engine, the JSONL manifest that recorded the asset, next-step note |

---

## `generate_music`

Background loops (default), one-off tracks, or stingers (short jingles with an
ending).

<!-- BEGIN:generate_music -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `description` | string |  | Character of the piece, e.g. 'mysterious forest exploration theme with a gentle pulse'. |
| `kind` | `music` \| `stinger` |  | music (default) or stinger (short jingle with an ending). |
| `genre` | string |  | e.g. 'orchestral', 'synthwave', 'chiptune', 'lo-fi hip hop', 'dark ambient'. |
| `mood` | string |  | Emotional character, e.g. 'tense', 'whimsical', 'melancholic', 'triumphant'. |
| `instruments` | string or array of string |  | String or list, e.g. ['strings', 'harp', 'soft choir pad']. |
| `energy` | string |  | low / medium / high / building. |
| `bpm` | number |  | Tempo 40-240. Loops are snapped to whole bars at this tempo. |
| `key` | string |  | e.g. 'D minor', 'F# major', 'Am'. |
| `beats_per_bar` | integer |  | Time signature numerator (default 4). |
| `structure` | string |  | Optional form notes, e.g. 'soft intro, building middle'. |
| `duration_seconds` | number |  | Length (loop length for loops). Default from settings (45 s). |
| `loop` | boolean |  | Seamless loop (default true for music, false for stingers). |
| `crossfade_seconds` | number |  | Loop crossfade length (default 0.75 s). |
| `vocals` | boolean |  | Allow vocals (default false: instrumental). |
| `lyrics` | string |  | Lyrics with [verse]/[chorus] tags (ComfyUI ACE-Step backend only). |
| `filename` | string |  | Desired file name (sanitized, extension added). Default derived from the description. |
| `project_path` | string |  | Game project root; omit to save to the plugin's music library. |
| `subdir` | string |  | Default: the project's music folder or the engine convention (e.g. res://audio/music). |
| `on_conflict` | `rename` \| `error` |  | If the file exists: add a numeric suffix (rename, default) or fail (error). Never overwrites. |
| `post_process` | object |  | Mastering overrides. Defaults per kind: sfx peak -1 dBTP; ui -20 LUFS; stinger/music -16 LUFS (-1 dBTP); ambience -24 LUFS (-3 dBTP); DC removal, silence trim, de-click fades (never on loops), lookahead limiting, 16-bit TPDF dither. Pass {"enabled": false} for the raw model output. |
| `post_process.enabled` | boolean | | false = skip mastering and keep the raw model output. |
| `post_process.target_lufs` | number | | Integrated loudness target, e.g. -16. |
| `post_process.peak_dbtp` | number | | Peak-normalize to this true-peak level instead of loudness. |
| `post_process.ceiling_dbtp` | number | | True-peak ceiling enforced by the limiter. |
| `post_process.trim_silence` | boolean | | Trim leading (and, for one-shots, trailing) silence. Default true. |
| `post_process.fade_out_seconds` | number | | Override the end fade (one-shots only; loops are never faded). |
| `post_process.mono` | boolean | | Downmix to mono (good for positional 2D/3D sounds). |
| `post_process.normalize` | boolean | | false = keep the level as generated (no loudness/peak normalization); limiting and fades still apply. |
| `post_process.remove_dc` | boolean | | Remove DC offset (default true). |
| `backend` | `auto` \| `agent_audio` \| `comfyui` \| `procedural` |  | Override the configured backend for this call. agent_audio = Stable Audio 3 Medium; comfyui = your local ComfyUI workflow (Stable Audio Open / ACE-Step: exact BPM, key, seed, lyrics). 'procedural' is a built-in synthesizer for quick placeholders (not AI); only use it when the user asks for placeholders or the AI backend is unavailable and the user agrees. |
| `seed` | integer |  | Seed for reproducible output where the backend supports it (ComfyUI, procedural). |
| `candidates` | integer |  | Generate this many takes and keep the best-scoring one (default from settings: 2 for SFX, 1 for music). |
| `min_score` | number |  | Quality gate 0-100: keep generating (up to max_attempts) until a take reaches it. |
| `max_attempts` | integer |  | Upper bound on takes when min_score is used. |
| `keep_alternates` | boolean |  | Also save the non-chosen takes into the project as name_altN (default: kept in plugin data only). |
| `output_format` | `wav` \| `ogg` |  | ogg (Vorbis, smaller; needs ffmpeg) is recommended for long music in shipped games. |
<!-- END:generate_music -->

**Example**

```json
{"project_path": "C:/Games/MyGodotGame", "genre": "orchestral", "description": "mysterious forest exploration theme",
 "instruments": ["pizzicato strings", "harp", "soft flute"], "energy": "low", "bpm": 90, "key": "D minor",
 "duration_seconds": 40, "output_format": "ogg"}
```

**What you get for a loop** (real output, procedural backend):

```text
engine_ref: res://audio/music/heroic_overworld_theme_loop.wav   embedded_loop: true
mastering:
  built seamless loop: 8.57s = 5 bars at 140 BPM, 0.75s equal-power crossfade, seam score 93/100
  loudness -14.4 -> -16.0 LUFS (-1.6 dB)
  final loudness -16.0 LUFS
  exported 16-bit PCM with TPDF dither + embedded loop points
quality: score 100, good; integrated -16.0 LUFS, true peak -3.4 dBTP, tempo matches (half-time)
```

Notes: loops are generated a little longer than requested (crossfade tail plus
margin), then cut to whole bars at `bpm`. With `post_process: {"enabled": false}`
the loop is still built but levels are left untouched. `lyrics` only reach backends that
support them (ComfyUI ACE-Step), and implies `vocals: true`. With Agent Audio,
BPM and key go into the prompt and are measured afterwards; `notes` says so.

---

## `generate_variations`

Several numbered takes of one sound (`name_v01.wav`, `name_v02.wav`, …) to
avoid repetition for frequent sounds. Each take gets a different prompt
nuance (unless `vary_prompt: false`) and a different seed. One failed take
doesn't stop the others.

<!-- BEGIN:generate_variations -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `description` | string | yes | What the sound should be: source, action, character, length (max 1000 characters). |
| `count` | integer |  | How many takes (1 to max_variations setting, default 3). |
| `duration_seconds` | number |  | Length in seconds. |
| `style` | string |  | Optional shared aesthetic, e.g. 'retro 8-bit', 'realistic', 'cartoon', 'dark fantasy'. |
| `filename` | string |  | Base name for the takes. |
| `project_path` | string |  | Game project root folder (absolute, or relative to Hermes's working directory). |
| `subdir` | string |  | Project-relative output folder. Default: the project's existing folder for this kind, or the engine convention. |
| `vary_prompt` | boolean |  | Add small wording variations per take (default true). |
| `post_process` | object |  | Mastering overrides. Defaults per kind: sfx peak -1 dBTP; ui -20 LUFS; stinger/music -16 LUFS (-1 dBTP); ambience -24 LUFS (-3 dBTP); DC removal, silence trim, de-click fades (never on loops), lookahead limiting, 16-bit TPDF dither. Pass {"enabled": false} for the raw model output. |
| `post_process.enabled` | boolean | | false = skip mastering and keep the raw model output. |
| `post_process.target_lufs` | number | | Integrated loudness target, e.g. -16. |
| `post_process.peak_dbtp` | number | | Peak-normalize to this true-peak level instead of loudness. |
| `post_process.ceiling_dbtp` | number | | True-peak ceiling enforced by the limiter. |
| `post_process.trim_silence` | boolean | | Trim leading (and, for one-shots, trailing) silence. Default true. |
| `post_process.fade_out_seconds` | number | | Override the end fade (one-shots only; loops are never faded). |
| `post_process.mono` | boolean | | Downmix to mono (good for positional 2D/3D sounds). |
| `post_process.normalize` | boolean | | false = keep the level as generated (no loudness/peak normalization); limiting and fades still apply. |
| `post_process.remove_dc` | boolean | | Remove DC offset (default true). |
| `backend` | `auto` \| `agent_audio` \| `comfyui` \| `procedural` |  | Override the configured backend for this call. agent_audio = Stable Audio 3 Medium; comfyui = your local ComfyUI workflow (Stable Audio Open / ACE-Step: exact BPM, key, seed, lyrics). 'procedural' is a built-in synthesizer for quick placeholders (not AI); only use it when the user asks for placeholders or the AI backend is unavailable and the user agrees. |
| `kind` | `sfx` \| `ui` |  | sfx (default) or ui; selects prompt style and mastering. |
| `seed` | integer |  | Base seed; take i uses seed + 1000*i so takes still differ (ComfyUI, procedural). |
| `candidates` | integer |  | Takes generated per variation, best kept (default 1). |
<!-- END:generate_variations -->

**Returns** `requested`, `generated`, `partial`, `takes` (each shaped like a
`generate_sound` result), `failures`, `engine`. Each variation is one take
unless `candidates` is set.

---

## `analyze_audio`

Read-only quality report for any audio file. Use it to check existing assets
or generated ones.

<!-- BEGIN:analyze_audio -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Absolute path, or project-relative/res:// path with project_path. WAV/FLAC (others need ffmpeg). |
| `project_path` | string |  | Game project root folder (absolute, or relative to Hermes's working directory). |
| `kind` | `sfx` \| `ui` \| `ambience` \| `music` \| `stinger` |  | Asset kind; selects analysis rules and the mastering profile. Default guessed from the file/folder name. |
| `bpm` | number |  | Expected tempo to check against. |
| `loop` | boolean |  | Judge it as a loop (default: true if the WAV has loop points). |
| `expected_seconds` | number |  | Expected length, to flag files that are much shorter. |
| `description` | string |  | Optional text to check semantic match (CLAP). |
<!-- END:analyze_audio -->

`kind` defaults to a guess from the file and folder names (`music`, `bgm`,
`theme` → music; `ambience`, `amb` → ambience; `stinger`, `jingle` → stinger;
`ui`, `click`, `button` → ui; otherwise sfx). WAV and FLAC are read directly;
Ogg, MP3 and others need ffmpeg.

**Returns** `metrics`, `issues` (code, severity, message,
`fixable_by_mastering`), `score`, `rank_score`, `verdict`, `embedded_loop`
(`{start, end}` or `null`), `semantic` (when `description` is given and CLAP
is configured), and `recommendations` (what `process_audio` can fix, and
which issues need regenerating).

---

## `process_audio`

Master, repair, loop or convert an existing file into a **new** file next to
it (default suffix `_mastered`, or `_loop`). The original is never modified.

<!-- BEGIN:process_audio -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | string | yes | Audio file: absolute path, or project-relative / res:// path together with project_path. |
| `project_path` | string |  | Game project root folder (absolute, or relative to Hermes's working directory). |
| `kind` | `sfx` \| `ui` \| `ambience` \| `music` \| `stinger` |  | Selects the mastering profile. |
| `loop` | boolean |  | Treat/build as a seamless loop. |
| `bpm` | number |  | Tempo in BPM (40-240). With loop, the loop is snapped to whole bars. |
| `beats_per_bar` | integer |  | Time signature numerator (default 4). |
| `loop_seconds` | number |  | Target loop length (snapped to bars when bpm is given). |
| `crossfade_seconds` | number |  | Loop crossfade length (default 0.75 s for music, up to 1.5 s for ambience). |
| `post_process` | object |  | Mastering overrides. Defaults per kind: sfx peak -1 dBTP; ui -20 LUFS; stinger/music -16 LUFS (-1 dBTP); ambience -24 LUFS (-3 dBTP); DC removal, silence trim, de-click fades (never on loops), lookahead limiting, 16-bit TPDF dither. Pass {"enabled": false} for the raw model output. |
| `post_process.enabled` | boolean | | false = skip mastering and keep the raw model output. |
| `post_process.target_lufs` | number | | Integrated loudness target, e.g. -16. |
| `post_process.peak_dbtp` | number | | Peak-normalize to this true-peak level instead of loudness. |
| `post_process.ceiling_dbtp` | number | | True-peak ceiling enforced by the limiter. |
| `post_process.trim_silence` | boolean | | Trim leading (and, for one-shots, trailing) silence. Default true. |
| `post_process.fade_out_seconds` | number | | Override the end fade (one-shots only; loops are never faded). |
| `post_process.mono` | boolean | | Downmix to mono (good for positional 2D/3D sounds). |
| `post_process.normalize` | boolean | | false = keep the level as generated (no loudness/peak normalization); limiting and fades still apply. |
| `post_process.remove_dc` | boolean | | Remove DC offset (default true). |
| `output_format` | `wav` \| `ogg` |  | wav (default) or ogg (Vorbis, needs ffmpeg). |
| `output_suffix` | string |  | Appended to the file name (default _mastered or _loop). |
<!-- END:process_audio -->

**Examples**

```json
{"project_path": "C:/Games/MyGodotGame", "path": "res://audio/music/old_theme.wav", "kind": "music", "loop": true, "bpm": 120, "loop_seconds": 32}
{"path": "D:/samples/door.wav", "kind": "sfx", "post_process": {"mono": true, "peak_dbtp": -3}}
```

**Returns** `path` (new file), `steps`, `loop`, `before` and `after` (score,
metrics, issue codes), `project_path` / `engine_ref` inside a project, and
`format_note` if Ogg was requested without ffmpeg (WAV is written instead).

---

## `inspect_game_audio`

Read-only project scan. Writes nothing.

<!-- BEGIN:inspect_game_audio -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_path` | string | yes | Game project root folder. |
| `max_assets` | integer |  | Cap on listed assets (default 200). |
<!-- END:inspect_game_audio -->

**Returns** `engine` (detected engine, version, marker file, extra flags such
as Godot C# use), `audio_asset_count`, `audio_assets` (path, engine ref, size,
WAV duration/rate/channels, `referenced_by`, `problem` for malformed files),
`audio_dirs`, `suggested_audio_dir` (+ `_ref`), `naming_style`,
`unreferenced_assets`, `missing_references` (code referring to audio files
that don't exist), `candidate_gaps` (event-like functions with no audio:
file, function, line, event, suggested description, confidence),
`code_integration_supported`, Godot's `audio_stream_players` and
`godot_executable`, plus scan statistics (`files_scanned`, `scan_truncated`,
`links_skipped`).

Detected engines and their markers: Godot (`project.godot`), Unity
(`Assets/` + `ProjectSettings/ProjectVersion.txt`), Unreal (`*.uproject`),
Defold (`game.project`), GameMaker (`*.yyp`), Bevy (`Cargo.toml` with
`bevy`), web (`package.json` with Phaser, Pixi, Three, Kaboom/Kaplay,
Excalibur, Babylon, PlayCanvas or Howler), LÖVE (`main.lua` using `love.`),
Pygame (requirements/pyproject or a script importing `pygame`).

---

## `prepare_game_audio`

Plan and produce every sound a feature needs in one call: SFX, ambience,
music and stingers together. Each sound is reused if a matching file exists,
otherwise generated into the right folder for its kind.

<!-- BEGIN:prepare_game_audio -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_path` | string | yes | Game project root folder (absolute, or relative to Hermes's working directory). |
| `feature` | string | yes | The gameplay feature, e.g. 'enemy hit and death feedback'. |
| `sounds` | array of object | yes | Sounds the feature needs (max 12). |
| `sounds[].name` | string | yes | Short asset name, e.g. 'enemy_hit' or 'forest_theme'. |
| `sounds[].kind` | `sfx` \| `ui` \| `ambience` \| `music` \| `stinger` |  | Default sfx. |
| `sounds[].genre` | string |  | music/stinger only |
| `sounds[].mood` | string |  | music/stinger only |
| `sounds[].bpm` | number |  | music/stinger only |
| `sounds[].key` | string |  | music/stinger only |
| `sounds[].loop` | boolean |  | music (default true) / ambience (default true) |
| `sounds[].description` | string | yes | Concrete sound description for generation. |
| `sounds[].duration_seconds` | number |  | Length in seconds. |
| `sounds[].variations` | integer |  | Numbered variation files for sfx/ui items (default 1; ignored for music, stingers and ambience). |
| `sounds[].reuse_existing` | boolean |  | Reuse a matching existing asset (default true). |
| `audio_dir` | string |  | Project-relative output folder. Default: detected convention per kind (sfx vs music vs ambience). |
| `candidates` | integer |  | Takes per sound (best kept). |
| `style` | string |  | Shared aesthetic for all sounds of the feature. |
| `dry_run` | boolean |  | Return the plan (reuse vs generate, folders, exact prompts) without generating anything. |
| `post_process` | object |  | Mastering overrides. Defaults per kind: sfx peak -1 dBTP; ui -20 LUFS; stinger/music -16 LUFS (-1 dBTP); ambience -24 LUFS (-3 dBTP); DC removal, silence trim, de-click fades (never on loops), lookahead limiting, 16-bit TPDF dither. Pass {"enabled": false} for the raw model output. |
| `post_process.enabled` | boolean | | false = skip mastering and keep the raw model output. |
| `post_process.target_lufs` | number | | Integrated loudness target, e.g. -16. |
| `post_process.peak_dbtp` | number | | Peak-normalize to this true-peak level instead of loudness. |
| `post_process.ceiling_dbtp` | number | | True-peak ceiling enforced by the limiter. |
| `post_process.trim_silence` | boolean | | Trim leading (and, for one-shots, trailing) silence. Default true. |
| `post_process.fade_out_seconds` | number | | Override the end fade (one-shots only; loops are never faded). |
| `post_process.mono` | boolean | | Downmix to mono (good for positional 2D/3D sounds). |
| `post_process.normalize` | boolean | | false = keep the level as generated (no loudness/peak normalization); limiting and fades still apply. |
| `post_process.remove_dc` | boolean | | Remove DC offset (default true). |
| `backend` | `auto` \| `agent_audio` \| `comfyui` \| `procedural` |  | Override the configured backend for this call. agent_audio = Stable Audio 3 Medium; comfyui = your local ComfyUI workflow (Stable Audio Open / ACE-Step: exact BPM, key, seed, lyrics). 'procedural' is a built-in synthesizer for quick placeholders (not AI); only use it when the user asks for placeholders or the AI backend is unavailable and the user agrees. |
| `music_dir` | string |  | With audio_dir: project-relative folder for music/stinger/ambience items (default: same as audio_dir). |
<!-- END:prepare_game_audio -->

**Example**

```json
{"project_path": "C:/Games/MyGodotGame", "feature": "boss fight", "style": "dark fantasy",
 "sounds": [
   {"name": "boss_theme", "kind": "music", "description": "intense battle", "bpm": 150, "key": "C minor", "duration_seconds": 45},
   {"name": "boss_roar", "description": "huge monster roar, close", "duration_seconds": 1.5, "variations": 2},
   {"name": "victory", "kind": "stinger", "description": "triumphant fanfare", "duration_seconds": 4}]}
```

**Returns** with `dry_run: true`, a `plan` (per sound: kind, final file stem,
folder, reuse vs generate, the existing asset, the exact prompt). Otherwise
`assets` (name, kind, `reused`/`generated`, path, engine ref, backend, quality
score/verdict, loop flag), `failures`, `partial`, `manifest`, `next_step`.
Names that would collide (including variation names) are rejected up front.
`variations` applies to sfx/ui items only. Item-level settings (`candidates`,
`backend`, `post_process`, `output_format`, `min_score`, `max_attempts`) override
the call-level ones.

---

## `integrate_game_audio`

Connect an asset to gameplay. **Godot 4:** edits a `.gd` script.
**Other engines:** returns a snippet and manual steps (`applied: false`).

<!-- BEGIN:integrate_game_audio -->
| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_path` | string | yes | Game project root folder (absolute, or relative to Hermes's working directory). |
| `action` | `preview` \| `apply` \| `revert` |  | Default preview. |
| `sound` | string |  | Asset path in the project (res://..., or project-relative). |
| `script` | string |  | Script to edit (res://... or project-relative), e.g. res://enemy.gd. |
| `function` | string |  | Function that represents the event, e.g. take_damage. |
| `anchor` | string |  | Optional: insert right after the first line in the function containing this text. |
| `mode` | `auto` \| `global` \| `2d` \| `3d` \| `music` \| `ambience` |  | One-shot player type (auto = 2D/3D by the script's base class), or music/ambience: a persistent looping player that fades in and crossfades when changed (call it e.g. in a level's _ready). |
| `fade_in_seconds` | number |  | music/ambience fade-in (default 1 s). |
| `volume_db` | number |  | Playback volume in dB (default 0). |
| `pitch_variation` | number |  | Random pitch range 0–0.3 to reduce repetition (default 0.05). |
| `run_engine_checks` | boolean |  | Run Godot headless import + parse when available (default true). |
| `integration_id` | string |  | Required for action='revert'. |
| `force` | boolean |  | Revert even if the file changed after the integration (default false). |
<!-- END:integrate_game_audio -->

**Modes (Godot):**

| `mode` | What is added | Typical use |
|---|---|---|
| `auto` | `2d`/`3d` if the script extends a 2D/3D node, else `global` | Most SFX |
| `global` | One-shot `AudioStreamPlayer` | UI, non-positional SFX |
| `2d` / `3d` | One-shot `AudioStreamPlayer2D/3D` at the node's position | Positional SFX |
| `music` | Looping `HLGA_Music` player added to the calling node, with fade-in; crossfades when another track starts on the same node; `Music` bus if present. It lives as long as that node (so put it on the level/menu root, or an autoload for music that spans scenes) | Level/menu music, called from `_ready` |
| `ambience` | Same as music, as `HLGA_Ambience`, `Ambience` bus | Level ambience |

One-shot players are parented to the current scene, so a death sound survives
`queue_free()`, and they use an `SFX` bus if one exists. When fired during
scene setup (from a `_ready()`), the player is added on the next frame and
autoplays.

**Typical flow:** `action: "preview"` (shows `diff` and `changes`, writes
nothing) → `action: "apply"` (returns `integration_id`, `changed_files`,
`backup_dir`, `validation` with `level` = `static_checked` / `engine_parsed` /
a failure, `static_checks`, Godot output, `playback: unverified…`) →
optionally `action: "revert"` with that `integration_id`. Applying the same
thing twice returns `already_integrated`.

**Unity:** asset placement plus a C# snippet
(`[SerializeField] AudioClip …; AudioSource.PlayClipAtPoint(…)`). **Unreal:** a
staged source WAV plus an editor-Python import snippet. **Other engines:**
manual steps.
