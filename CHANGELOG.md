# Changelog

## 0.2.1 — 2026-10-20

Plugin-catalog readiness.

### Changed
- Child processes (Agent Audio server, CLAP scorer, Godot, ffmpeg) no longer
  inherit `HERMES_*` variables or credential-looking variables.
- `plugin.yaml`: added `requires_hermes: ">=0.21.6"`; `homepage` now points at
  this plugin's repository.
- `pyproject.toml`: removed the `dev` dependency group, whose `pytest<9` pin
  conflicted with Hermes's own and made `hermes plugins validate --install-deps`
  fail.
- README: added a "What it does on your machine" disclosure section; SECURITY.md
  covers setup-time downloads, Godot import scripts and unattended runs.

## 0.2.0 — 2026-10-19

### Added
- **Music:** `generate_music` for loops, one-off tracks and stingers (genre,
  mood, instruments, energy, BPM, key, time signature, structure,
  vocals/lyrics). Bar-aligned seamless loops with embedded `smpl` loop points.
- **Ambience:** `kind: "ambience"` in `generate_sound`, looped by default.
- **Quality engine:** best-of-N `candidates`, quality gate (`min_score`,
  `max_attempts`), alternates kept, pure-Python analysis (BS.1770 loudness,
  true peak, clipping, DC, silence, abrupt end, phase, spectrum, tempo, loop
  seam), per-kind mastering (loudness targets, lookahead limiter, fades,
  dither).
- **Optional CLAP prompt-match scoring** in a separate environment
  (`scripts/setup_clap.*`).
- **ComfyUI backend:** patches your exported API workflow by node type
  (Stable Audio Open; ACE-Step 1.0/1.5 with exact BPM, key, time signature,
  seed and lyrics), localhost-only by default, FLAC decoding built in.
- **Per-kind routing:** `music_backend` setting.
- **New tools:** `analyze_audio` (quality report) and `process_audio`
  (master/repair/loop into a new file).
- **Godot:** `mode: "music"` / `"ambience"` persistent looping players with
  fade-in and crossfade; separate SFX/music/ambience folders per engine.
- Ogg Vorbis export via ffmpeg; procedural placeholder music and ambience.
- Docs: `TOOLS.md` (generated parameter tables), `USAGE.md`,
  `CONFIGURATION.md`, `QUALITY.md`, `DEVELOPMENT.md`, ComfyUI guide.

### Fixed (from an independent review)
- One-shot SFX fired from `_ready()` during scene setup failed silently in Godot.
- Loudness read 1–3 dB low on files longer than 20 s.
- Loops shorter than twice the crossfade crashed; now rejected up front.
- `#` inside a GDScript string confused the function parser.
- Seeded variations were identical.
- ffmpeg WAV size patch could hit metadata instead of the data chunk.
- Only the first prompt node was patched in multi-sampler ComfyUI workflows.
- Sub-bar loops were reported as bar-aligned; limiter/loudness reporting
  understated gain reduction; "too short" check rarely fired;
  `normalize_peak_db: null` didn't disable leveling; short stingers passed
  planning; name collisions with variation files; UTF-8 BOM scripts refused;
  1-sample loop offset in Godot (fixed with a guard frame).
- Turning mastering off on a music loop also skipped building the loop; the
  loop is now still built with levels untouched.
- `process_audio` now reports when Ogg was requested without ffmpeg.
- Docs fact-checked against the code; every parameter now has a description;
  `post_process.normalize` / `remove_dc`, `generate_variations` `seed` /
  `candidates`, and `prepare_game_audio` `music_dir` added to the schemas.

## 0.1.0 — 2026-10-19

- First release: native Hermes plugin with `audio_status`, `generate_sound`,
  `generate_variations`, `inspect_game_audio`, `prepare_game_audio`,
  `integrate_game_audio`; Agent Audio (Stable Audio 3 Medium) over stdio MCP;
  procedural placeholder SFX; engine detection for 9 engine families; Godot 4
  script integration with preview/apply/revert and headless validation;
  `game-audio` skill; install scripts; docs and tests.
