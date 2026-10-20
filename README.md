# Hermes Local Game Audio

A native [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that turns
Hermes into a local game sound designer **and composer**. Ask for gameplay work
— *"finish the enemy hit and death feedback"*, *"give the forest level
mysterious exploration music and night ambience"* — and Hermes finds the gaps,
generates sound effects, ambience, music loops and stingers **on your own
computer**, checks and masters every take, drops the files into the project
without overwriting anything, wires them to the right events, and validates
the result.

- **Local AI generation, no cloud:**
  [Agent Audio](https://github.com/AIEGOBOT/agent-audio) (Stable Audio 3
  Medium) and/or your **ComfyUI** (Stable Audio Open, **ACE-Step 1.5** with
  exact BPM, key, seed and lyrics). Per-kind routing, e.g. ComfyUI for music
  and Agent Audio for SFX.
- **Music:** loops (default), one-off tracks and stingers from genre, mood,
  instruments, energy, BPM and key. Loops are bar-aligned, phase-matched and
  crossfaded, with embedded loop points that Godot detects on import.
- **Quality engine (pure Python, no extra packages):** best-of-N takes, an
  optional quality gate, BS.1770 loudness, true peak, clipping, DC, silence,
  abrupt-end, stereo-phase, spectrum, tempo and loop-seam analysis; mastering
  to game targets with a lookahead limiter and dither; optional **CLAP**
  prompt-match scoring. Every result reports its score, issues and
  verification level.
- **Understands game projects:** Godot, Unity, Unreal, Defold, GameMaker,
  Bevy, web (Phaser/Pixi/Three/Kaboom…), LÖVE, Pygame. Separate SFX / music /
  ambience folders follow each engine's conventions.
- **Godot 4 auto-integration:** one-shot SFX (global/2D/3D) and persistent,
  looping, crossfading music and ambience players. Every change is previewed,
  backed up, validated by headless Godot, and revertible. Unity and Unreal get
  reviewable snippets instead.
- **Safe by default:** writes stay inside the project, links and path
  traversal are refused, nothing is overwritten, no shell strings, ComfyUI only
  on localhost, and zero Python dependencies added to Hermes.

## What it does on your machine

Read this before installing. Details are in [docs/SECURITY.md](docs/SECURITY.md).

- **Network:** the plugin itself only talks to your ComfyUI at `comfyui_url`
  (loopback only unless you turn on `comfyui_allow_remote`). It makes no other
  network calls and has no telemetry or usage reporting. The optional setup
  scripts download from third parties when *you* run them: `install.*` only
  copies files locally, while `setup_clap.*` installs PyTorch and Transformers from PyPI
  (and download.pytorch.org on Linux) and downloads the CLAP model from Hugging
  Face. Agent Audio's own installer downloads its models (~7.4 GB).
- **Processes it starts:** the Agent Audio model server (only while a
  generation tool runs, stopped afterwards; nothing stays running in the
  background), the optional CLAP scorer, `ffmpeg` for Ogg export, and headless
  **Godot** to import and syntax-check your project after an `integrate_game_audio`
  apply. Godot's import step runs your project's editor/tool scripts, as it does
  when you open the project. All are started from argument lists, never a shell,
  with timeouts, and without any `HERMES_*` variables or credential-looking
  variables (`*_API_KEY`, `*_TOKEN`, `*_SECRET`…) in their environment.
- **Files it reads outside its own data:** the game projects you point it at
  (optionally limited by `allowed_project_roots`), audio files you pass to
  `analyze_audio`/`process_audio`, your ComfyUI workflow JSON files, and your
  Agent Audio checkout (auto-detected in a few folders under your home
  directory when `agent_audio_checkout` is empty).
- **Files it writes:** new audio files and reversible, backed-up script edits
  inside your project; everything else under
  `<HERMES_HOME>/plugin-data/hermes-local-game-audio/`.
- **Credentials:** none. It reads no secrets, stores no tokens and has no
  sign-in.
- **Hermes integration:** public plugin APIs only (`register_tool`,
  `register_skill`, `register_command` and a `pre_llm_call` hook that adds a
  one-line skill hint to game-related messages; turn it off with
  `inject_skill_hint: false`). No self-updater: updates come only through
  `hermes plugins update`.

## Tools

| Tool | What it does |
|---|---|
| `audio_status` | Routing, readiness of each backend, quality tools, RAM/disk guidance, setup steps |
| `generate_sound` | SFX / UI / ambience: best of N takes, analysed, mastered, saved |
| `generate_music` | Music loops, tracks and stingers with genre/mood/instruments/BPM/key |
| `generate_variations` | Numbered takes of one SFX (footsteps, hits) |
| `analyze_audio` | Engineer-style quality report for any file (+ CLAP match) |
| `process_audio` | Master, repair, loop or convert an existing file into a new file |
| `inspect_game_audio` | Read-only engine detection, audio inventory, broken refs, silent events |
| `prepare_game_audio` | Plan and produce a feature's SFX, ambience and music in one call |
| `integrate_game_audio` | `preview` / `apply` / `revert` hookups (SFX, music, ambience) |

It also ships the `hermes-local-game-audio:game-audio` skill, a `/game-audio`
status command, and a one-line hint that points Hermes at the skill when a
message looks like game work.

## Quick start

```powershell
.\scripts\install.ps1                      # Windows (PowerShell), from this folder
.\scripts\setup_clap.ps1                   # optional: prompt-match scoring (~1.5 GB)
```
```bash
./scripts/install.sh && ./scripts/setup_clap.sh   # macOS / Linux / WSL
```

Then set up at least one AI backend (see [docs/INSTALL.md](docs/INSTALL.md)):
Agent Audio (CPU is fine, about 25 GB disk, 32 GB-class RAM recommended) and/or
ComfyUI ([examples/comfyui](examples/comfyui/README.md), best with a GPU).
Start a new Hermes session and run `/game-audio deep`. Without a backend you
can still use `backend: procedural` placeholders, which include simple music.

Try it on the bundled demo:

> In examples/godot-demo, finish the enemy hit and death feedback with sound,
> and add a looping heroic chiptune theme at 140 BPM that starts with the level.

## Documentation

| Read this | For |
|---|---|
| [docs/INSTALL.md](docs/INSTALL.md) | Requirements, installing the plugin, Agent Audio, ComfyUI, CLAP, ffmpeg; updating and uninstalling |
| [docs/USAGE.md](docs/USAGE.md) | What to ask Hermes, worked scenarios, reading results, engines, FAQ |
| [docs/TOOLS.md](docs/TOOLS.md) | Every tool, every parameter, every result field, with examples |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting, environment variables, data-folder layout, example `config.yaml` |
| [docs/QUALITY.md](docs/QUALITY.md) | How prompts, takes, analysis, mastering, loops and CLAP produce quality, and their limits |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Every error code and common problems |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works inside, and the design decisions |
| [docs/SECURITY.md](docs/SECURITY.md) | Filesystem, process and network safety rules |
| [docs/LICENSING.md](docs/LICENSING.md) | Plugin vs model licenses (Stable Audio, ACE-Step, CLAP) |
| [docs/FEATURES.md](docs/FEATURES.md) | Feature matrix: what's tested, partial or unverified; latest test run |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | File-by-file layout, running tests, extending, release checklist |
| [examples/comfyui/README.md](examples/comfyui/README.md) | Setting up ComfyUI workflows (Stable Audio Open, ACE-Step 1.5) |
| [examples/godot-demo/README.md](examples/godot-demo/README.md) | The demo game and its headless checks |
| [skills/game-audio/SKILL.md](skills/game-audio/SKILL.md) | The workflow Hermes follows |
| [CHANGELOG.md](CHANGELOG.md) | What changed in each version |

## Development

```bash
cd tests
uv run --no-project --with "pytest>=8,<9" pytest -rs    # offline suite, no models needed
hermes plugins doctor .. --ci && hermes plugins validate ..
python ../scripts/gen_tool_docs.py --check               # tool docs match the schemas
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the test fakes, the optional
real-world tests and how to add tools, backends or engines.

MIT licensed. Model weights are not included and have their own terms — see
[docs/LICENSING.md](docs/LICENSING.md).
