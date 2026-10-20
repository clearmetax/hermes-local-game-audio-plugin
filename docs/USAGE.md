# Using the plugin

You talk to Hermes normally. The bundled `game-audio` skill tells Hermes when
and how to use the tools, so you don't need to name them. This guide shows
what to ask, what happens, and how to steer the result.

## Before the first request

1. Start a new Hermes session after installing and run `/game-audio deep`.
   It shows which backend handles SFX and which handles music, whether each
   is ready, and the exact setup steps for anything missing.
2. If no AI backend is ready yet, you can still work with placeholders: say
   *"use placeholder sounds for now"* and Hermes uses the built-in procedural
   synth. Every placeholder is labelled `is_ai_model: false`, so you can find
   and replace them later.

## Everyday requests

| You say | What happens |
|---|---|
| "Finish the enemy hit and death feedback in this Godot project." | Inspects the project, finds `take_damage` / `die` with no sound, generates two SFX, previews then applies the script edits, validates with Godot, reports the files and integration ids. |
| "Make a 2-second heavy metallic impact." | `generate_sound` into the plugin library (no project) and reports the path and quality. |
| "Add calm, mysterious exploration music to the forest level, around 90 BPM in D minor, and start it when the level loads." | `generate_music` (seamless loop, mastered to -16 LUFS), then `integrate_game_audio` with `mode: "music"` on the level script's `_ready`. |
| "Give the cave level ambience: dripping water, distant wind, no melody." | Ambience loop at -24 LUFS, integrated with `mode: "ambience"`. |
| "I need a victory jingle and a game-over sting, 8-bit style." | Two stingers with clear endings, not looped. |
| "Make 4 footstep variations on gravel." | `generate_variations`: `footstep_on_gravel_v01.wav` … `_v04.wav`. |
| "Do the whole boss fight audio: music, roar, attacks, and a victory sting." | One `prepare_game_audio` call that plans and produces everything, reusing any matching files already in the project. |
| "Check the audio in this project for problems." | `inspect_game_audio`, plus `analyze_audio` on the assets: loudness, clipping, late starts, broken references, silent events. |
| "This menu music isn't looping cleanly — fix it." | `process_audio` with `loop: true` (and `bpm` if known) writes `menu_theme_loop.wav` next to the original. |
| "Undo the last audio change." | `integrate_game_audio` `action: "revert"` with the integration id. |

## Getting the best quality

- **Describe the sound physically** — source, action, weight, texture, space,
  length: "small glass potion bottle uncorked, bright pop, then a soft fizz"
  beats "potion sound".
- **For music, give the musical facts:** genre, mood, 2–4 instruments, energy,
  tempo and key, e.g. *"orchestral, low energy, pizzicato strings + harp +
  flute, 90 BPM, D minor"*. Keep one palette across a game.
- **Ask for more takes on important assets:** *"try 3 takes and keep the best"*
  sets `candidates: 3`. *"keep trying until it scores at least 85"* sets a
  quality gate (`min_score`).
- **Set up the CLAP scorer** (`scripts/setup_clap.*`) so takes are also ranked
  by how well they match the description, not only by technical quality.
- **Use ComfyUI + ACE-Step 1.5 for music** if you have a GPU: tempo, key, seed
  and lyrics are then enforced by the model instead of only requested.
- **Listen.** Metrics and CLAP can't judge taste. Alternates are kept in
  `plugin-data/.../candidates/`, or ask Hermes to `keep_alternates` and
  they're saved next to the chosen file as `_altN`.

## Reading a result

Every generation reports:

- **where** the file went (`engine_ref` such as `res://audio/music/forest_theme_loop.wav`);
- **who made it** (`backend`, `is_ai_model`);
- **how good it is** (`quality.score`, `verdict`, remaining `issues`, which
  issues mastering fixed, loudness and true peak, tempo, loop seam score);
- **what was done** (`mastering` steps, the exact `prompt_used`, candidate scores);
- **caveats** (`notes`, e.g. "Agent Audio cannot force a tempo; 90 BPM was
  requested in the prompt and ~91 BPM was measured").

Integration results report `validation.level`: `static_checked` (file
checks passed), `engine_parsed` (Godot imported the asset and parsed the
script), or a failure with the errors. Playback is always reported as
unverified unless a game run or trigger test actually ran.

## Engines

| Engine | What the plugin does |
|---|---|
| **Godot 4** | Places files in `audio/sfx`, `audio/music`, `audio/ambience` (or your existing folders), edits `.gd` scripts with preview/apply/revert, validates headless. |
| Godot 3 | Detection, inventory and file placement; script edits are refused (Godot 4 syntax). |
| **Unity** | Files in `Assets/Audio/SFX` / `Assets/Audio/Music` (or yours); returns a C# snippet; Unity creates the `.meta` file on import. Hermes can apply the snippet with its normal editing tools. |
| **Unreal** | Source WAVs in `SourceAudio/SFX` / `SourceAudio/Music`; returns an editor-Python import snippet and Blueprint steps. |
| Web, Defold, GameMaker, Bevy, LÖVE, Pygame | Detection, inventory, conventional folders (`public/assets/audio/…`, `assets/…`, `datafiles/…`); code is wired by Hermes's normal editing tools. |

## Without a game project

Leave out the project and sounds go to the library folder
(`<HERMES_HOME>/plugin-data/hermes-local-game-audio/library/{sfx,music,ambience}`).
Useful for trailers, prototypes and sound packs.

## FAQ

**Does anything go to the cloud?** No. Generation runs on your computer
(Agent Audio, or ComfyUI on localhost). The only downloads are the one-time
model installs you run yourself.

**How long does it take?** Depends on your hardware. On CPU with Agent Audio,
expect minutes for a short SFX and longer for music. Keep music loops to
30–45 s on CPU, or use ComfyUI on a GPU. The plugin's own analysis and
mastering adds about a second per SFX and roughly 15 s for a 45 s track.

**Will it overwrite my files?** Never. New files get unique names, edits to
scripts are backed up and revertible, and `process_audio` writes new files.

**Can it make songs with vocals?** With ComfyUI + ACE-Step 1.5, yes: pass
`lyrics` (with `[verse]`/`[chorus]` tags). Agent Audio is instrumental-only
in practice and ignores lyrics (the result says so).

**Can I use the generated audio commercially?** That depends on the model's
license, not this plugin's. See [LICENSING.md](LICENSING.md).

**How do I make Hermes stop adding sound?** Say so ("no audio for this
task"). The skill tells Hermes to respect silence. You can also turn off
`inject_skill_hint`.

**Where are the files Hermes made?** Every result has the path, and each
project has a manifest in `plugin-data/.../manifests/` listing every asset the
plugin generated for it.
