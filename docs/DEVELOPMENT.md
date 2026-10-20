# Development guide

## Repository layout

| Path | Role |
|---|---|
| `plugin.yaml` | Hermes manifest: name, version, declared tools/hooks, `config_schema` (Desktop settings form) |
| `__init__.py` | `register(ctx)`: registers the 9 tools, the `game-audio` skill, the `pre_llm_call` hint hook and the `/game-audio` command |
| `schemas.py` | Tool schemas, i.e. what the model reads. Source of the parameter tables in `docs/TOOLS.md` |
| `tools.py` | Tool handlers: validation, folder/naming conventions, analyze/process, game workflow, Godot integration records, revert |
| `pipeline.py` | Generation pipeline: prompt builders, `Spec`, `Generator` (takes → validate → analyse → master → CLAP → rank → publish), manifests |
| `settings.py` | Settings defaults, `HLGA_*` overrides, data-folder resolution |
| `errors.py` | `AudioPluginError` and the stable error codes |
| `safety.py` | Project-root confinement, link refusal, safe names, exclusive publish, atomic edits, disk-space checks |
| `wav.py` | RIFF/WAVE parsing (PCM 8/16/24/32, float 32/64, extensible), validation, 16-bit encode with dither, `smpl` loop chunks |
| `dsp.py` | Pure-Python DSP: BS.1770 loudness, true peak, limiter, DC filter, fades, trim, FFT spectrum, stereo correlation, tempo, loop building and seam metrics, dither |
| `analysis.py` | Measurements → issues → score/verdict per kind |
| `mastering.py` | Per-kind mastering profiles and the `master()` chain |
| `codecs_io.py` | ffmpeg discovery/decoding/Ogg encoding, built-in FLAC decoder |
| `audio_backend/base.py` | Backend interface (`AudioBackend`, `Session`, `GenerationRequest`, `BackendStatus`, capabilities) |
| `audio_backend/__init__.py` | Backend selection and per-kind routing (`resolve_choice`, `make_backend`) |
| `audio_backend/agent_audio.py` | Agent Audio adapter: checkout discovery, sidecar and `hermes_mcp` transports, error mapping |
| `audio_backend/mcp_stdio.py` | Minimal stdio MCP client (JSON-RPC 2.0) and isolated child environment |
| `audio_backend/comfyui.py` | ComfyUI adapter: workflow analysis/patching by node type, HTTP queue/poll/download, localhost guard |
| `audio_backend/procedural.py`, `procedural_music.py` | Placeholder synth: SFX recipes, BPM/key-exact chiptune music, ambience |
| `audio_backend/diagnostics.py` | RAM/disk/OS report with Agent Audio's resource guidance |
| `semantic/__init__.py`, `semantic/clap_worker.py` | Optional CLAP scorer: plugin-side client and the worker run in its own venv |
| `game_adapters/scan.py` | Bounded project walk, function extraction per language, event classification, naming styles |
| `game_adapters/base.py` | Engine-agnostic inventory (references, missing refs, gaps) and manual integration |
| `game_adapters/godot.py` | Godot detection, `res://` refs, script edit planner (SFX and music/ambience helpers), static and engine checks, Godot discovery |
| `game_adapters/unity.py`, `unreal.py`, `others.py`, `detect.py` | Other engines and detection order |
| `skills/game-audio/SKILL.md` | The workflow Hermes follows |
| `scripts/install.ps1`, `scripts/install.sh` | Validate, copy runtime files into `HERMES_HOME/plugins`, doctor, enable (`-Update` / `--update` replaces an existing copy and keeps a backup) |
| `scripts/setup_clap.ps1`, `scripts/setup_clap.sh` | Create the CLAP venv, download the model, set `clap_python` |
| `scripts/gen_tool_docs.py` | Regenerate / check the parameter tables in `docs/TOOLS.md` |
| `examples/godot-demo/` | Demo game and headless Godot checks (`trigger_test.gd`, `music_test.gd`, `ready_test.gd`) |
| `examples/comfyui/` | ComfyUI guide and a Stable Audio Open API workflow |
| `tests/` | Test suite (see below) |
| `docs/` | User and developer documentation |

## Running the tests

The plugin root is itself a Python package (it has `__init__.py`), so run
pytest **from the `tests/` folder**:

```bash
cd tests
uv run --no-project --with "pytest>=8,<9" pytest -rs
# or, with any Python 3.11+ that has pytest:
python -m pytest -rs
```

No models, network or extra packages are needed for the default suite. It
uses:

- `tests/fake_mcp_server.py`: a stdlib stand-in for `agent_audio.mcp_server`
  that speaks real MCP stdio, with failure modes selected by `FAKE_MCP_MODE`
  (`ok`, `error`, `slow`, `crash`, `crash2`, `badwav`, `notready`,
  `wrongpath`, `notools`);
- `tests/fake_comfyui.py`: an in-process ComfyUI HTTP API that records patched
  workflows and returns real FLAC files (with valid CRCs), with
  `fail="reject"|"exec"` and `slow=True`;
- `tests/fixtures/ace_step_15_api.json`: an ACE-Step 1.5 graph built from the
  node schemas in ComfyUI's source;
- `examples/godot-demo`: copied to a temp folder per test.

`conftest.py` isolates every test: a temporary `HLGA_DATA_DIR`, no checkout
auto-discovery, CLAP off, one candidate per request.

### Optional real-world tests

| Variable | Enables |
|---|---|
| `HLGA_GODOT_EXECUTABLE=/path/to/godot` | `test_e2e_godot.py`: SFX trigger test, music loop/import test, `_ready()` scene-setup test |
| `HLGA_REAL_AGENT_AUDIO_CHECKOUT=/path/to/agent-audio` | Real stdio MCP round trip with Agent Audio's server (no model needed) |
| `HLGA_REAL_GENERATION=1` (with the above) | Real Stable Audio 3 Medium inference (needs installed models) |
| `HLGA_TEST_CLAP_PYTHON=/path/to/clap-venv/python` | Real CLAP scoring |
| ffmpeg on PATH | FLAC-vs-ffmpeg bit-exact tests and Ogg export test |

### Other checks

```bash
hermes plugins doctor . --ci        # discovery, manifest, import, register(ctx), declared vs registered
hermes plugins validate .           # catalog checks incl. security scan and dependency policy
uvx ruff check .                    # lint (config in pyproject.toml)
python scripts/gen_tool_docs.py --check
```

## Extending

- **New tool:** add the schema to `schemas.py` (and to `ALL`), the handler to
  `tools.py` (decorate with `@_respond`, add to `HANDLERS`), the name to
  `provides_tools` in `plugin.yaml`, a marker block in `docs/TOOLS.md`, then run
  `scripts/gen_tool_docs.py`. `test_registration.py` checks that all four agree.
- **New backend:** subclass `AudioBackend` (`status`, `session` yielding a
  `Session` with `generate(request) -> Path`, `supports`/`supports_for`), add
  it to `BACKENDS` and `make_backend` in `audio_backend/__init__.py`, and add
  its choice to `plugin.yaml` and the `_BACKEND` enum in `schemas.py`. The
  backend writes a WAV (or anything `codecs_io.decode_any` handles) to
  `request.output_path`; the pipeline does the rest.
- **New engine:** subclass `EngineAdapter` with `detect`, `default_audio_dir`,
  optional `engine_ref`/`find_references`/`manual_integration`, and add it to
  `ADAPTERS` in `detect.py` (most specific first).
- **New setting:** add it to `DEFAULTS` in `settings.py` and `config_schema` in
  `plugin.yaml`, and document it in `docs/CONFIGURATION.md`
  (`test_docs.py` enforces this).

## Release checklist

- [ ] `cd tests && pytest -rs` passes (with Godot, and ffmpeg if available)
- [ ] `hermes plugins doctor . --ci` and `hermes plugins validate .` pass
- [ ] `python scripts/gen_tool_docs.py --check` and `ruff check .` pass
- [ ] Version bumped in `plugin.yaml`, `pyproject.toml`, `skills/game-audio/SKILL.md`, and `CHANGELOG.md`
- [ ] `/game-audio deep` shows `ready=True` on a machine with a real backend
- [ ] One real SFX and one real music generation produce valid files; record OS, RAM/GPU, backend and timings in `docs/FEATURES.md`
- [ ] Demo scenario through a real Hermes chat: diff previewed, applied, sounds heard in the editor
- [ ] No WAVs, model files, `.godot/`, `__pycache__` or venvs in the release
