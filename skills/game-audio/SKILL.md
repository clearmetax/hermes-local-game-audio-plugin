---
name: game-audio
description: Add sound effects, ambience and music to a game with locally generated, quality-checked audio — decide what audio the work needs, reuse existing assets, generate missing SFX/music on this computer, master and loop them, wire them into gameplay, validate, and report honestly.
version: 0.2.1
---

# Game audio (local generation)

Use this skill when you build or change a game and the result would feel
unfinished without sound: hits, deaths, jumps, pickups, UI clicks, explosions,
footsteps, ambience for a level, background music for a menu or level,
victory / game-over stingers. Also use it when the user asks for a sound or
music directly.

Do **not** add audio when the user asked for silence, a muted mode, a
prototype "without sound", or when the task has nothing to do with what the
player hears. Don't add a soundtrack just because the tool exists — add music
when the task is about mood, menus, levels or polish, or the user asks.

Tools (toolset `game_audio`): `audio_status`, `inspect_game_audio`,
`prepare_game_audio`, `generate_sound`, `generate_music`,
`generate_variations`, `analyze_audio`, `process_audio`, `integrate_game_audio`.

## Workflow

1. **Readiness, once per session.** `audio_status` with `deep_check: true`.
   Read `routing` (which backend does SFX and which does music) and each
   backend's `ready`. If the needed backend isn't ready, show the user its
   `setup_steps` and ask whether to continue with placeholders
   (`backend: "procedural"`) or wait. Never switch to placeholders silently,
   never accept model licenses for the user.

2. **Inspect before generating.** `inspect_game_audio` on the project root:
   existing assets, folders, naming style, broken references, silent events.
   Gaps are heuristics — read the code before acting on one.

3. **Reuse first.** Use suitable existing audio. `prepare_game_audio` reuses
   files whose names match requested sounds unless `reuse_existing: false`.

4. **Plan the feature's audio** (names in the project's naming style):
   - SFX `kind: "sfx"`: UI 0.1–0.3 s (`kind: "ui"`), hits/footsteps 0.2–0.6 s,
     jumps/pickups 0.3–0.8 s, explosions 1–2.5 s. 2–4 `variations` only for very
     frequent sounds.
   - Ambience `kind: "ambience"`: 15–30 s seamless bed.
   - Music `kind: "music"`: a 30–60 s seamless loop per level/menu/combat state;
     give `genre`, `mood`, `instruments`, `energy`, `bpm` and `key`, and keep a
     shared style across the game (same genre/instrument palette, related keys).
   - Stingers `kind: "stinger"`: 2–5 s with a clear ending (victory, defeat,
     level-up, discovery).

5. **Generate.** One `prepare_game_audio` call for the whole feature (it mixes
   kinds), or `generate_sound` / `generate_music` individually. Generation can
   take minutes on CPU — tell the user it's running.
   - For important assets ask for `candidates: 2-3` (best is kept) or a quality
     gate `min_score: 85` with `max_attempts`.
   - Music loops: keep the default `loop: true`; the plugin bar-aligns,
     crossfades and embeds loop points. Prefer `output_format: "ogg"` for long
     music when ffmpeg is available.

6. **Check the result** before integrating. Each result has `quality.score`,
   `quality.verdict`, `quality.issues`, the candidate table, `notes` (e.g. tempo
   requested but not enforceable) and, when CLAP is set up,
   `quality.semantic.points` (how well it matches the description). If the
   verdict is `poor`, or semantic points are low (< 40), regenerate with a more
   concrete description or more candidates instead of shipping it. Use
   `analyze_audio` on existing assets and `process_audio` to fix level, silence,
   clipping-prone peaks or to turn a file into a loop (it writes a new file).

7. **Integrate.**
   - Godot 4 SFX: `integrate_game_audio` `action: "preview"`, check the diff,
     then `"apply"`. Use `anchor` to place the call after a specific line (e.g.
     after `health -= amount`) so it doesn't play when a guard returns early.
   - Godot 4 music/ambience: `mode: "music"` (or `"ambience"`) on the function
     that starts the level or menu (usually `_ready` of the level/menu root);
     it adds a looping player to that node with a fade-in and crossfades when a
     different track starts. It stops when that node is freed, so music meant to
     span scenes belongs on an autoload.
     Typical `volume_db`: music -6 to -10, ambience -12 to -18.
   - Unity / Unreal / other engines: you get a snippet and manual steps; apply
     the code yourself with your editing tools, following the project's existing
     audio pattern. If the project has an AudioManager or SFX autoload, call it
     instead of the plugin helper.

8. **Report precisely:** each asset (engine path, kind, duration, backend,
   quality score; say so if it came from the placeholder synth), loudness for
   music, loop status, changed files with `integration_id`s, the validation
   level (`static_checked` / `engine_parsed` / failure), and that playback is
   **unverified** unless you ran the game or an automated trigger test.

## Writing descriptions

SFX — physical source, action, weight, texture, space, length:
- "heavy steel sword striking a wooden shield, sharp transient, short metallic ring"
- "small glass potion bottle uncorked, bright pop, then a soft fizz"
- "soft UI button click, plastic, very short, dry"

Ambience — layers and distance, not just a mood:
- "night forest: distant crickets, light wind through leaves, occasional far owl, no melody"

Music — genre, character, instruments, energy, tempo, key:
- genre "orchestral", description "mysterious forest exploration theme",
  instruments ["pizzicato strings", "harp", "soft flute"], energy "low", bpm 90, key "D minor"
- genre "synthwave", description "neon city chase", energy "high", bpm 140, key "A minor"
- kind "stinger", genre "8-bit chiptune", description "short triumphant victory fanfare", bpm 150

Describe musical characteristics; don't ask to imitate a living artist or a
specific copyrighted recording. Put the shared aesthetic in `style`/`genre` so
a game has one sound world.

## Troubleshooting

- `BACKEND_NOT_CONFIGURED` / `BACKEND_NOT_READY`: show `setup_steps`; the user
  installs Agent Audio or configures ComfyUI and accepts model terms.
- `GENERATION_TIMEOUT`: shorter duration (SFX ≤ 3 s, music ≤ 45 s on CPU).
- `INVALID_OUTPUT`: unusable take. Another take is only tried automatically when
  `candidates` > 1 or a `min_score` gate is set; otherwise retry once yourself.
- `FILE_EXISTS` with `on_conflict: "error"`: pick another name; never delete the
  user's file.
- `CONFLICT` on revert: the script changed after integration; only `force: true`
  with the user's agreement.
- Engine parse failure after integration: read the errors; revert with the
  `integration_id` if the edit caused them.
