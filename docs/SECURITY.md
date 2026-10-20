# Security model

Hermes plugins run in-process with your user permissions; this plugin is not a
sandbox. These are the rules it enforces itself.

## Filesystem

- **Project confinement.** Every project path is resolved and must stay inside
  the project root. `..`, absolute paths, drive letters and `res://..` are
  rejected. Optional `allowed_project_roots` limits which projects can be used at all.
- **No links.** A symlinked project root, and any symlink or Windows junction
  between the root and a target file or folder, is refused. Inventory scans do
  not follow links.
- **No overwrites.** New sounds are published with an exclusive hardlink (or
  `O_EXCL` create) — an existing file is never replaced. Name clashes get a
  numeric suffix or a `FILE_EXISTS` error.
- **Reversible edits.** Script edits are previewable, backed up before writing,
  applied atomically only if the file did not change in between, and revertible.
  A revert refuses to clobber later edits unless `force` is passed.
- **Read-only inspection.** `inspect_game_audio` never writes. Files over 1.5 MB
  are not read as text; scans stop at 25,000 files and skip VCS, build, cache
  and engine-import folders.
- Filenames are reduced to `[A-Za-z0-9_-]`, max 64 characters; Windows reserved
  names are replaced.
- Disk space is checked before writing (refuses below 200 MB free).

## Processes

- The model server is started from an **argument list, never a shell string**,
  with Python isolation (`-I`, no `PYTHONPATH`/`PYTHONHOME`/`VIRTUAL_ENV`/user
  site). The only variables added are `AGENT_AUDIO_HOME` (when configured) and
  `PYTHONNOUSERSITE`, `PYTHONUTF8`, `PYTHONUNBUFFERED`.
- **No Hermes state or secrets in child processes.** Every child (model server,
  CLAP scorer, Godot, ffmpeg) gets a copy of the environment with all
  `HERMES_*` variables removed (so approval, YOLO or non-interactive modes are
  never inherited) and every variable whose name looks like a credential
  (`*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, …) removed.
- The plugin never prompts or waits for a person, so it behaves the same under
  cron, the messaging gateway and other unattended runs; every child process has
  a timeout.
- Prompts reach the model as JSON-RPC arguments; Agent Audio passes them as
  `--flag=value` arguments to its own subprocess.
- One generation at a time; each tool call starts and stops its own server.
  On timeout the server is terminated, then killed (process tree on Windows).
- Godot and ffmpeg are run with fixed arguments and timeouts. Godot's headless
  `--import` runs the project's own editor/tool scripts, exactly as opening the
  project in the editor would.
- `analyze_audio` / `process_audio` accept absolute paths only outside a
  project, honour `allowed_project_roots`, refuse links, and `process_audio`
  always writes a new file next to the source.

## Network and privacy

- The only network calls the plugin makes go to ComfyUI at `comfyui_url`. Non-loopback
  hosts are refused unless `comfyui_allow_remote` is enabled. Workflows are
  copied and patched in memory; your workflow files are never modified.
- The optional CLAP scorer runs as a separate process (argument list, isolated
  `-I` mode, `HF_HUB_OFFLINE=1`) and only reads the plugin's own take files.
- Agent Audio's generation runs with `HF_HUB_OFFLINE=1`; its one-time installer
  downloads runtime and models.
- The optional `scripts/setup_clap.*` (run by you, not by the plugin) installs
  PyTorch, Transformers and NumPy from PyPI (CPU wheels from download.pytorch.org
  on Linux) and downloads the CLAP model from Hugging Face once.
- No telemetry or usage reporting. No credentials are read or stored. No
  self-updater: the plugin never fetches or replaces its own files.
- No cloud audio API exists in the code. There is no silent fallback.
- Logs contain tool names and error codes, not prompts or project file contents.
- Agent Audio's stderr tail (last ~1.5 KB) is included in backend error details
  to help debugging; review before sharing.

## Data hygiene

- Model weights, caches and generated media are listed in `.gitignore`.
- Plugin state lives in `<HERMES_HOME>/plugin-data/hermes-local-game-audio/`.
- Generated audio placed in your project is yours to commit or ignore.

## Reporting

Report plugin issues privately to the maintainer. Report Agent Audio issues per
its own `SECURITY.md`.
