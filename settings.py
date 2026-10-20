"""Plugin settings and data locations.

Settings are read through Hermes's plugin-scoped config API
(``plugins.entries.hermes-local-game-audio.settings.*``) when a context is
available. Environment variables prefixed ``HLGA_`` override them, which keeps
tests and local development independent of a Hermes install.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PLUGIN_NAME = "hermes-local-game-audio"

HARD_MAX_DURATION = 120.0  # SFX/UI/ambience one-shots; never exceed regardless of settings.
HARD_MAX_MUSIC_DURATION = 380.0  # Agent Audio's own upper bound.
HARD_MAX_CANDIDATES = 8
HARD_MAX_VARIATIONS = 8
HARD_MAX_TIMEOUT = 1800

DEFAULTS: dict[str, Any] = {
    "backend": "auto",
    "agent_audio_transport": "sidecar",
    "agent_audio_checkout": "",
    "agent_audio_home": "",
    "agent_audio_mcp_server": "agent-audio",
    "default_duration_seconds": 2.0,
    "max_duration_seconds": 30.0,
    "generation_timeout_seconds": 600,
    "max_variations": 4,
    "output_dir": "",
    "allowed_project_roots": [],
    "godot_executable": "",
    "inject_skill_hint": True,
    # music
    "music_backend": "auto",
    "default_music_seconds": 45.0,
    "max_music_seconds": 180.0,
    # quality
    "default_candidates": 2,
    "default_music_candidates": 1,
    "max_candidates": 4,
    "clap_python": "",
    "clap_model": "laion/larger_clap_general",
    "output_format": "wav",
    "ffmpeg_executable": "",
    # comfyui
    "comfyui_url": "http://127.0.0.1:8188",
    "comfyui_workflow_sfx": "",
    "comfyui_workflow_music": "",
    "comfyui_allow_remote": False,
}


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, list):
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return []
                if value.startswith("["):
                    value = json.loads(value)
                else:
                    value = value.split(os.pathsep)
            return [str(v) for v in value if str(v).strip()]
        return "" if value is None else str(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    values: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    data_dir: Path = field(default_factory=lambda: default_data_dir())

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - convenience
        values = self.__dict__.get("values", {})
        if item in values:
            return values[item]
        raise AttributeError(item)

    @property
    def max_duration(self) -> float:
        return max(0.1, min(float(self.values["max_duration_seconds"]), HARD_MAX_DURATION))

    @property
    def default_duration(self) -> float:
        return max(0.1, min(float(self.values["default_duration_seconds"]), self.max_duration))

    @property
    def timeout(self) -> int:
        return max(10, min(int(self.values["generation_timeout_seconds"]), HARD_MAX_TIMEOUT))

    @property
    def max_variations(self) -> int:
        return max(1, min(int(self.values["max_variations"]), HARD_MAX_VARIATIONS))

    @property
    def max_music_duration(self) -> float:
        return max(1.0, min(float(self.values["max_music_seconds"]), HARD_MAX_MUSIC_DURATION))

    @property
    def max_candidates(self) -> int:
        return max(1, min(int(self.values["max_candidates"]), HARD_MAX_CANDIDATES))

    @property
    def candidates_dir(self) -> Path:
        return self.data_dir / "candidates"

    @property
    def library_dir(self) -> Path:
        custom = str(self.values.get("output_dir") or "").strip()
        return Path(custom).expanduser() if custom else self.data_dir / "library"

    @property
    def renders_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def manifests_dir(self) -> Path:
        return self.data_dir / "manifests"

    def public(self) -> dict[str, Any]:
        out = dict(self.values)
        out["data_dir"] = str(self.data_dir)
        out["library_dir"] = str(self.library_dir)
        return out


def default_data_dir() -> Path:
    override = os.environ.get("HLGA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    try:  # Hermes's sanctioned per-plugin data root (follows the active profile).
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home()) / "plugin-data" / PLUGIN_NAME
    except Exception:
        home = os.environ.get("HERMES_HOME")
        base = Path(home).expanduser() if home else Path.home() / ".hermes"
        return base / "plugin-data" / PLUGIN_NAME


def load_settings(get_config: Callable[[str, Any], Any] | None = None) -> Settings:
    """Resolve settings on every call so edits in config.yaml apply without restart."""
    values: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        value: Any = default
        if get_config is not None:
            try:
                value = get_config(key, default)
            except Exception:
                value = default
        env = os.environ.get("HLGA_" + key.upper())
        if env is not None:
            value = env
        values[key] = _coerce(key, value)
    return Settings(values=values, data_dir=default_data_dir())
