"""Tool schemas — what the model reads to decide when and how to call each tool."""

from __future__ import annotations

_POST = {
    "type": "object",
    "description": (
        "Mastering overrides. Defaults per kind: sfx peak -1 dBTP; ui -20 LUFS; stinger/music -16 LUFS (-1 dBTP); "
        "ambience -24 LUFS (-3 dBTP); DC removal, silence trim, de-click fades (never on loops), lookahead limiting, "
        "16-bit TPDF dither. Pass {\"enabled\": false} for the raw model output."
    ),
    "properties": {
        "enabled": {"type": "boolean"},
        "target_lufs": {"type": "number", "description": "Integrated loudness target, e.g. -16."},
        "peak_dbtp": {"type": "number", "description": "Peak-normalize to this true-peak level instead of loudness."},
        "ceiling_dbtp": {"type": "number", "description": "True-peak ceiling enforced by the limiter."},
        "trim_silence": {"type": "boolean"},
        "fade_out_seconds": {"type": "number"},
        "mono": {"type": "boolean", "description": "Downmix to mono (good for positional 2D/3D sounds)."},
        "normalize": {"type": "boolean", "description": "false = keep the level as generated (no loudness/peak normalization); limiting and fades still apply."},
        "remove_dc": {"type": "boolean", "description": "Remove DC offset (default true)."},
    },
}

_QUALITY = {
    "candidates": {"type": "integer", "description": "Generate this many takes and keep the best-scoring one (default from settings: 2 for SFX, 1 for music)."},
    "min_score": {"type": "number", "description": "Quality gate 0-100: keep generating (up to max_attempts) until a take reaches it."},
    "max_attempts": {"type": "integer", "description": "Upper bound on takes when min_score is used."},
    "keep_alternates": {"type": "boolean", "description": "Also save the non-chosen takes into the project as name_altN (default: kept in plugin data only)."},
    "output_format": {"type": "string", "enum": ["wav", "ogg"], "description": "ogg (Vorbis, smaller; needs ffmpeg) is recommended for long music in shipped games."},
}

_BACKEND = {
    "type": "string",
    "enum": ["auto", "agent_audio", "comfyui", "procedural"],
    "description": (
        "Override the configured backend for this call. agent_audio = Stable Audio 3 Medium; comfyui = your local ComfyUI "
        "workflow (Stable Audio Open / ACE-Step: exact BPM, key, seed, lyrics). 'procedural' is a built-in synthesizer for quick "
        "placeholders (not AI); only use it when the user asks for placeholders or the AI backend is unavailable and the user agrees."
    ),
}

AUDIO_STATUS = {
    "name": "audio_status",
    "description": (
        "Check whether local sound-effect generation is ready: selected backend (Agent Audio / Stable Audio 3 Medium "
        "running on this computer, or the procedural placeholder synth), runtime and model readiness, RAM/disk guidance, "
        "output folders, Godot availability and exact setup steps. Call this before the first generation in a session or "
        "when a generation fails. deep_check=true starts the local model server to verify readiness (a few seconds)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "deep_check": {"type": "boolean", "description": "Start the backend and ask it for readiness. Default false."},
            "project_path": {"type": "string", "description": "Optional game project folder to report engine detection for."},
        },
    },
}

GENERATE_SOUND = {
    "name": "generate_sound",
    "description": (
        "Generate ONE sound effect, UI sound or ambience bed locally from a text description. Each take is validated, "
        "analysed (loudness, true peak, clipping, timing, spectrum), mastered for games and ranked; the best take is saved. "
        "With project_path it is written into that game project (never overwriting an existing file) and the "
        "engine path (e.g. res://audio/sfx/x.wav) is returned. Describe the sound concretely: source, material, "
        "action, character, length, e.g. 'heavy steel sword striking a wooden shield, sharp transient, short ring'. "
        "Keep durations short (0.3–4 s for most SFX); CPU generation can take minutes. Generating a WAV does not "
        "connect it to gameplay — use integrate_game_audio for that."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "What the sound should be (max 1000 characters)."},
            "kind": {"type": "string", "enum": ["sfx", "ui", "ambience"], "description": "sfx (default), ui (short interface sounds), ambience (background bed, looped by default)."},
            "loop": {"type": "boolean", "description": "Build a seamless loop (ambience)."},
            "duration_seconds": {"type": "number", "description": "Length in seconds. Default from settings (2 s)."},
            "style": {"type": "string", "description": "Optional aesthetic, e.g. 'retro 8-bit', 'realistic', 'cartoon', 'dark fantasy', 'sci-fi'."},
            "filename": {"type": "string", "description": "Desired file name (sanitized; .wav added). Default derived from the description."},
            "project_path": {"type": "string", "description": "Game project root. Omit to save to the plugin's sound library."},
            "subdir": {"type": "string", "description": "Project-relative folder. Default: the project's existing audio folder or the engine convention."},
            "on_conflict": {"type": "string", "enum": ["rename", "error"], "description": "If the file exists: add a numeric suffix (default) or fail. Never overwrites."},
            "post_process": _POST,
            "backend": _BACKEND,
            "seed": {"type": "integer", "description": "Seed for reproducible output (procedural and ComfyUI backends)."},
            **_QUALITY,
        },
        "required": ["description"],
    },
}

GENERATE_VARIATIONS = {
    "name": "generate_variations",
    "description": (
        "Generate several distinct takes of one sound effect (e.g. 3 footsteps or 4 hit variants to avoid repetition). "
        "Files get numbered names (name_v01.wav ...). Partial failures are reported per take; successful takes are kept."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "count": {"type": "integer", "description": "How many takes (1 to max_variations setting, default 3)."},
            "duration_seconds": {"type": "number"},
            "style": {"type": "string"},
            "filename": {"type": "string", "description": "Base name for the takes."},
            "project_path": {"type": "string"},
            "subdir": {"type": "string"},
            "vary_prompt": {"type": "boolean", "description": "Add small wording variations per take (default true)."},
            "post_process": _POST,
            "backend": _BACKEND,
        },
        "required": ["description"],
    },
}

INSPECT_GAME_AUDIO = {
    "name": "inspect_game_audio",
    "description": (
        "Read-only scan of a game project: detects the engine (Godot, Unity, Unreal, Defold, GameMaker, Bevy, web "
        "frameworks, LÖVE, Pygame) from real project markers, inventories audio assets and where they are referenced, "
        "finds broken audio references, unreferenced files, the audio folder and naming convention in use, and lists "
        "event-like functions (hit, jump, pickup, death, UI click...) that play no sound. Gaps are heuristic suggestions. "
        "Use before generating so existing sounds are reused and conventions respected."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Game project root folder."},
            "max_assets": {"type": "integer", "description": "Cap on listed assets (default 200)."},
        },
        "required": ["project_path"],
    },
}

PREPARE_GAME_AUDIO = {
    "name": "prepare_game_audio",
    "description": (
        "Plan and produce the sounds for one game feature in a project: for each requested sound, reuse a suitable "
        "existing asset when one matches, otherwise generate it locally into the project's audio folder with "
        "collision-safe names. Returns a manifest of reused and generated assets (with engine paths) to pass to "
        "integrate_game_audio. Use dry_run=true to see the plan without generating."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {"type": "string"},
            "feature": {"type": "string", "description": "The gameplay feature, e.g. 'enemy hit and death feedback'."},
            "sounds": {
                "type": "array",
                "description": "Sounds the feature needs (max 12).",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Short asset name, e.g. 'enemy_hit' or 'forest_theme'."},
                        "kind": {"type": "string", "enum": ["sfx", "ui", "ambience", "music", "stinger"], "description": "Default sfx."},
                        "genre": {"type": "string", "description": "music/stinger only"},
                        "mood": {"type": "string", "description": "music/stinger only"},
                        "bpm": {"type": "number", "description": "music/stinger only"},
                        "key": {"type": "string", "description": "music/stinger only"},
                        "loop": {"type": "boolean", "description": "music (default true) / ambience (default true)"},
                        "description": {"type": "string", "description": "Concrete sound description for generation."},
                        "duration_seconds": {"type": "number"},
                        "variations": {"type": "integer", "description": "Numbered variation files for sfx/ui items (default 1; ignored for music, stingers and ambience)."},
                        "reuse_existing": {"type": "boolean", "description": "Reuse a matching existing asset (default true)."},
                    },
                    "required": ["name", "description"],
                },
            },
            "audio_dir": {"type": "string", "description": "Project-relative output folder. Default: detected convention per kind (sfx vs music vs ambience)."},
            "candidates": {"type": "integer", "description": "Takes per sound (best kept)."},
            "style": {"type": "string", "description": "Shared aesthetic for all sounds of the feature."},
            "dry_run": {"type": "boolean"},
            "post_process": _POST,
            "backend": _BACKEND,
        },
        "required": ["project_path", "feature", "sounds"],
    },
}

INTEGRATE_GAME_AUDIO = {
    "name": "integrate_game_audio",
    "description": (
        "Connect a sound asset to a gameplay event with a minimal, backed-up, reversible edit, then validate. "
        "Godot 4: adds a preload constant, a small one-shot player helper and one call at the top of the chosen "
        "function (or after an anchor line), runs static checks and — if Godot is installed — a headless import and "
        "script parse. Use action='preview' first to show the diff, 'apply' to write it, 'revert' with integration_id "
        "to undo. Unity/Unreal/other engines return a reviewable snippet and manual steps instead of editing. "
        "Playback is never claimed as verified unless actually tested."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "project_path": {"type": "string"},
            "action": {"type": "string", "enum": ["preview", "apply", "revert"], "description": "Default preview."},
            "sound": {"type": "string", "description": "Asset path in the project (res://..., or project-relative)."},
            "script": {"type": "string", "description": "Script to edit (res://... or project-relative), e.g. res://enemy.gd."},
            "function": {"type": "string", "description": "Function that represents the event, e.g. take_damage."},
            "anchor": {"type": "string", "description": "Optional: insert right after the first line in the function containing this text."},
            "mode": {"type": "string", "enum": ["auto", "global", "2d", "3d", "music", "ambience"], "description": "One-shot player type (auto = 2D/3D by the script's base class), or music/ambience: a persistent looping player that fades in and crossfades when changed (call it e.g. in a level's _ready)."},
            "fade_in_seconds": {"type": "number", "description": "music/ambience fade-in (default 1 s)."},
            "volume_db": {"type": "number", "description": "Playback volume in dB (default 0)."},
            "pitch_variation": {"type": "number", "description": "Random pitch range 0–0.3 to reduce repetition (default 0.05)."},
            "run_engine_checks": {"type": "boolean", "description": "Run Godot headless import + parse when available (default true)."},
            "integration_id": {"type": "string", "description": "Required for action='revert'."},
            "force": {"type": "boolean", "description": "Revert even if the file changed after the integration (default false)."},
        },
        "required": ["project_path"],
    },
}

GENERATE_MUSIC = {
    "name": "generate_music",
    "description": (
        "Generate game music locally: background loops (default), one-off tracks, or short stingers (victory, game over, "
        "level up). Describe genre, mood, instruments, energy, tempo and key; loops are bar-aligned to the BPM, phase-"
        "matched and crossfaded so they repeat seamlessly, and get embedded loop points (Godot loops them automatically). "
        "Output is mastered to -16 LUFS / -1 dBTP and checked for gaps, noise, tempo and seam quality. With the ComfyUI "
        "ACE-Step backend, BPM/key/lyrics are enforced by the model; otherwise they are requested in the prompt and measured. "
        "Music takes minutes on CPU: prefer 30-60 s loops."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Character of the piece, e.g. 'mysterious forest exploration theme with a gentle pulse'."},
            "kind": {"type": "string", "enum": ["music", "stinger"], "description": "music (default) or stinger (short jingle with an ending)."},
            "genre": {"type": "string", "description": "e.g. 'orchestral', 'synthwave', 'chiptune', 'lo-fi hip hop', 'dark ambient'."},
            "mood": {"type": "string"},
            "instruments": {"description": "String or list, e.g. ['strings', 'harp', 'soft choir pad'].", "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]},
            "energy": {"type": "string", "description": "low / medium / high / building."},
            "bpm": {"type": "number", "description": "Tempo 40-240. Loops are snapped to whole bars at this tempo."},
            "key": {"type": "string", "description": "e.g. 'D minor', 'F# major', 'Am'."},
            "beats_per_bar": {"type": "integer", "description": "Time signature numerator (default 4)."},
            "structure": {"type": "string", "description": "Optional form notes, e.g. 'soft intro, building middle'."},
            "duration_seconds": {"type": "number", "description": "Length (loop length for loops). Default from settings (45 s)."},
            "loop": {"type": "boolean", "description": "Seamless loop (default true for music, false for stingers)."},
            "crossfade_seconds": {"type": "number", "description": "Loop crossfade length (default 0.75 s)."},
            "vocals": {"type": "boolean", "description": "Allow vocals (default false: instrumental)."},
            "lyrics": {"type": "string", "description": "Lyrics with [verse]/[chorus] tags (ComfyUI ACE-Step backend only)."},
            "filename": {"type": "string"},
            "project_path": {"type": "string", "description": "Game project root; omit to save to the plugin's music library."},
            "subdir": {"type": "string", "description": "Default: the project's music folder or the engine convention (e.g. res://audio/music)."},
            "on_conflict": {"type": "string", "enum": ["rename", "error"]},
            "post_process": _POST,
            "backend": _BACKEND,
            "seed": {"type": "integer"},
            **_QUALITY,
        },
    },
}

ANALYZE_AUDIO = {
    "name": "analyze_audio",
    "description": (
        "Measure an audio file's quality the way an audio engineer would: integrated loudness (LUFS), true peak, "
        "clipping, DC offset, silence/late start, abrupt ending, stereo phase, spectrum balance, tempo, and loop seam "
        "quality (embedded WAV loop points are detected). Returns a 0-100 score, issues, and which ones process_audio can fix. "
        "With description and a configured CLAP scorer it also rates how well the audio matches the text. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path, or project-relative/res:// path with project_path. WAV/FLAC (others need ffmpeg)."},
            "project_path": {"type": "string"},
            "kind": {"type": "string", "enum": ["sfx", "ui", "ambience", "music", "stinger"]},
            "bpm": {"type": "number", "description": "Expected tempo to check against."},
            "loop": {"type": "boolean", "description": "Judge it as a loop (default: true if the WAV has loop points)."},
            "expected_seconds": {"type": "number"},
            "description": {"type": "string", "description": "Optional text to check semantic match (CLAP)."},
        },
        "required": ["path"],
    },
}

PROCESS_AUDIO = {
    "name": "process_audio",
    "description": (
        "Master or repair an existing audio file into a NEW file next to it (the original is never modified): "
        "loudness-normalize to a game target, limit true peak, remove DC, trim silence, add de-click fades, downmix to "
        "mono, or turn it into a seamless loop (bar-aligned with bpm) with embedded loop points; optionally export Ogg."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "project_path": {"type": "string"},
            "kind": {"type": "string", "enum": ["sfx", "ui", "ambience", "music", "stinger"], "description": "Selects the mastering profile."},
            "loop": {"type": "boolean"},
            "bpm": {"type": "number"},
            "beats_per_bar": {"type": "integer"},
            "loop_seconds": {"type": "number", "description": "Target loop length (snapped to bars when bpm is given)."},
            "crossfade_seconds": {"type": "number"},
            "post_process": _POST,
            "output_format": {"type": "string", "enum": ["wav", "ogg"]},
            "output_suffix": {"type": "string", "description": "Appended to the file name (default _mastered or _loop)."},
        },
        "required": ["path"],
    },
}

GENERATE_VARIATIONS["parameters"]["properties"]["kind"] = {
    "type": "string", "enum": ["sfx", "ui"], "description": "sfx (default) or ui; selects prompt style and mastering."}
GENERATE_VARIATIONS["parameters"]["properties"]["seed"] = {
    "type": "integer", "description": "Base seed; take i uses seed + 1000*i so takes still differ (ComfyUI, procedural)."}
GENERATE_VARIATIONS["parameters"]["properties"]["candidates"] = {
    "type": "integer", "description": "Takes generated per variation, best kept (default 1)."}
PREPARE_GAME_AUDIO["parameters"]["properties"]["music_dir"] = {
    "type": "string", "description": "With audio_dir: project-relative folder for music/stinger/ambience items (default: same as audio_dir)."}

ALL = [AUDIO_STATUS, GENERATE_SOUND, GENERATE_MUSIC, GENERATE_VARIATIONS, ANALYZE_AUDIO, PROCESS_AUDIO,
       INSPECT_GAME_AUDIO, PREPARE_GAME_AUDIO, INTEGRATE_GAME_AUDIO]

# Shared wording for parameters that mean the same thing in every tool. Explicit
# descriptions above always win; this only fills gaps so every parameter is documented.
_COMMON = {
    "project_path": "Game project root folder (absolute, or relative to Hermes's working directory).",
    "description": "What the sound should be: source, action, character, length (max 1000 characters).",
    "duration_seconds": "Length in seconds.",
    "style": "Optional shared aesthetic, e.g. 'retro 8-bit', 'realistic', 'cartoon', 'dark fantasy'.",
    "subdir": "Project-relative output folder. Default: the project's existing folder for this kind, or the engine convention.",
    "filename": "Desired file name (sanitized, extension added). Default derived from the description.",
    "mood": "Emotional character, e.g. 'tense', 'whimsical', 'melancholic', 'triumphant'.",
    "on_conflict": "If the file exists: add a numeric suffix (rename, default) or fail (error). Never overwrites.",
    "seed": "Seed for reproducible output where the backend supports it (ComfyUI, procedural).",
    "dry_run": "Return the plan (reuse vs generate, folders, exact prompts) without generating anything.",
    "kind": "Asset kind; selects analysis rules and the mastering profile. Default guessed from the file/folder name.",
    "expected_seconds": "Expected length, to flag files that are much shorter.",
    "path": "Audio file: absolute path, or project-relative / res:// path together with project_path.",
    "loop": "Treat/build as a seamless loop.",
    "bpm": "Tempo in BPM (40-240). With loop, the loop is snapped to whole bars.",
    "beats_per_bar": "Time signature numerator (default 4).",
    "crossfade_seconds": "Loop crossfade length (default 0.75 s for music, up to 1.5 s for ambience).",
    "output_format": "wav (default) or ogg (Vorbis, needs ffmpeg).",
    "enabled": "false = skip mastering and keep the raw model output.",
    "trim_silence": "Trim leading (and, for one-shots, trailing) silence. Default true.",
    "fade_out_seconds": "Override the end fade (one-shots only; loops are never faded).",
}


def _fill(props: dict) -> None:
    for name, p in props.items():
        if not p.get("description") and name in _COMMON:
            p["description"] = _COMMON[name]
        if isinstance(p.get("properties"), dict):
            _fill(p["properties"])
        if isinstance(p.get("items"), dict) and isinstance(p["items"].get("properties"), dict):
            _fill(p["items"]["properties"])


for _schema in ALL:
    _fill(_schema["parameters"]["properties"])
