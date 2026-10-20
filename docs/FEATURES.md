# Feature matrix and test report

Status key: **Tested** = exercised by an automated test that ran ·
**Partial** = deliberately limited · **Not verified** = could not be run here.

## Generation

| Feature | Status |
|---|---|
| SFX, UI sounds, ambience beds (`generate_sound`, `kind`) | Tested (procedural, fake Agent Audio MCP server, fake ComfyUI) |
| Music loops, one-off tracks, stingers (`generate_music`) | Tested (procedural, fake ComfyUI/ACE-Step, fake Agent Audio) |
| Music prompt tags: genre, mood, instruments, energy, BPM, key, structure, loop/stinger form, instrumental | Tested |
| Best-of-N takes, ranking, alternates kept / published | Tested |
| Quality gate (`min_score`, `max_attempts`) | Tested |
| CLAP prompt-match ranking (optional sidecar) | Tested with the real `laion/larger_clap_general` model on CPU; ranking logic tested with stubs |
| Variations with per-take failure reporting | Tested |
| Honest capability notes (tempo/key/lyrics not enforceable on a backend) | Tested |
| Ogg Vorbis export (ffmpeg) and WAV fallback | Tested |

## Backends

| Backend | Status |
|---|---|
| Agent Audio sidecar over MCP stdio | Tested against the **real** `agent_audio.mcp_server` (status, not-ready error) and a protocol-faithful fake (success, errors, crash, timeout, bad WAV, wrong path, incompatible server) |
| Real Stable Audio 3 Medium inference | **Not verified** here (needs installed models + accepted licenses + RAM); test provided (`HLGA_REAL_GENERATION=1`) |
| Agent Audio via Hermes `ctx.call_mcp` | Tested with stubbed `call_mcp` |
| ComfyUI: workflow analysis + patching (Stable Audio Open, ACE-Step 1.5) | Tested against node schemas from ComfyUI source @ 8f04fd6 |
| ComfyUI HTTP flow (queue, poll, errors, timeout + interrupt, FLAC download/decode, node availability check, localhost-only) | Tested against a fake ComfyUI server |
| Real ComfyUI inference | **Not verified** here (needs a ComfyUI install + models); same code path as the fake |
| Procedural placeholder synth (SFX recipes, key/BPM-exact chiptune music, ambience) | Tested |
| Per-kind routing (`backend`, `music_backend`, auto) | Tested |

## Quality engine

| Feature | Status |
|---|---|
| BS.1770-4 integrated / short-term / momentary loudness | Tested against the reference tone (-3.01 LUFS ±0.05) |
| True-peak estimate (4x oversampling) | Tested on intersample overs |
| Lookahead limiter (ceiling never exceeded, click-free) | Tested |
| Tempo estimation (half/double aware) | Tested on click tracks (90/128/150 BPM) and synthesized music |
| Clipping, DC, late start, abrupt end, silence/gaps, phase, noise-like spectrum | Tested |
| Mastering profiles per kind (loudness, ceilings, fades, trim, mono, DC) | Tested |
| Seamless loops: bar-aligned, phase-matched, equal-power crossfade, seam score | Tested |
| Embedded RIFF `smpl` loop points | Tested; **Godot 4.3 imports them as a forward loop** (verified headless) |
| 16-bit TPDF dither | Tested |
| Built-in FLAC decoder | Tested bit-exact vs ffmpeg (16/24-bit, mono, max compression, verbatim) |
| `analyze_audio`, `process_audio` (new file, never overwrites) | Tested |

## Hermes and game integration

| Feature | Status |
|---|---|
| Manifest, `register(ctx)`, 9 tools, 1 hook, skill, `/game-audio` | Tested (`plugins doctor --ci`, `plugins validate`, unit tests) |
| Install script, enable, toolset visible, registry dispatch, settings via `hermes config set`, uninstall keeps data | Tested on Linux in an isolated `HERMES_HOME`; `install.ps1` / `setup_clap.ps1` not run (no Windows here) |
| Engine detection (Godot 3/4, Unity, Unreal, Defold, GameMaker, Bevy, web, LÖVE, Pygame) | Tested |
| Separate SFX / music / ambience folders per engine convention | Tested (Godot, Unity) |
| Godot 4 SFX integration (preview/apply/revert, idempotent, anchors, CRLF/spaces) | Tested; headless parse + **automated trigger test PASS** |
| Godot 4 music/ambience integration (persistent looping player, fade-in, crossfade) | Tested; headless parse + **automated music test PASS** (player playing, looping) |
| Unity / Unreal / others | Partial by design: asset placement + snippets/steps |
| Audible quality and musical taste | Not automatically verifiable; listen in the editor |

## Test run (this build)

Environment: Linux x86_64, Python 3.12 (tests) / 3.14 (Hermes), Hermes Agent
`main` @ 349e4f7e, Agent Audio v0.1.0, ComfyUI source @ 8f04fd6 (node
schemas), Godot 4.3-stable, CLAP `laion/larger_clap_general` on CPU (torch 2.7.1).

```text
$ cd tests && uv run --no-project --with "pytest>=8,<9" pytest -rs
  (HLGA_GODOT_EXECUTABLE, HLGA_REAL_AGENT_AUDIO_CHECKOUT, HLGA_TEST_CLAP_PYTHON set)
223 passed, 1 skipped (real Stable Audio inference: needs installed models)
  includes 25 documentation checks: every tool/parameter/setting/error code/source
  file documented, generated tool tables current, all links and anchors resolve,
  versions agree, install scripts copy every runtime file

$ hermes plugins doctor . --ci      -> OK, 9 tool(s), 1 hook(s)
$ hermes plugins validate .         -> Validation passed (no dependencies, security scan safe)
$ ruff check .                      -> All checks passed
$ python scripts/gen_tool_docs.py --check -> docs/TOOLS.md is up to date
```

Godot end-to-end scenarios (headless Godot 4.3, procedural backend):
enemy hit + death SFX (trigger test FAIL → PASS → FAIL after revert),
background music loop (`loop_mode=1` imported from the embedded loop points,
`HLGA_Music` playing and looping), SFX fired from `_ready()` during scene setup.

An independent review found 11 issues, all fixed with regression tests
(`tests/test_review_regressions.py`), including: SFX from `_ready()` during
scene setup, loudness under-reading above 20 s, loops shorter than the
crossfade, `#` inside GDScript strings, identical seeded variations,
multi-sampler ComfyUI workflows, and a one-sample loop-point offset in Godot
(fixed with a guard frame).

## Known limitations

- Real AI inference (Agent Audio or ComfyUI) has not been run through this
  plugin in this environment. Run `/game-audio deep` and one short generation
  on your machine.
- Agent Audio v0.1 cannot enforce BPM, key, seed or lyrics, and on Windows CPU
  long music can hit its 540 s inference limit. ComfyUI + ACE-Step 1.5 can
  enforce them.
- Objective metrics and CLAP don't judge taste or fit to *your* game.
- Pure-Python analysis/mastering adds roughly 15 s for a 45 s stereo track
  (about a second for SFX), per take. That's small next to generation time.
- Godot integration inserts at the function start or after an anchor; it
  doesn't reason about control flow. Always preview.
