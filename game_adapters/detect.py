"""Engine detection from real project markers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import EngineAdapter
from .godot import GodotAdapter
from .others import BevyAdapter, DefoldAdapter, GameMakerAdapter, LoveAdapter, PygameAdapter, WebAdapter
from .unity import UnityAdapter
from .unreal import UnrealAdapter

# Most specific markers first.
ADAPTERS: list[type[EngineAdapter]] = [
    GodotAdapter, UnityAdapter, UnrealAdapter, DefoldAdapter, GameMakerAdapter,
    BevyAdapter, WebAdapter, LoveAdapter, PygameAdapter,
]


def detect_engine(root: Path) -> tuple[EngineAdapter, dict[str, Any]]:
    for cls in ADAPTERS:
        try:
            info = cls.detect(root)
        except OSError:
            info = None
        if info:
            return cls(root), info
    return EngineAdapter(root), {"engine": "unknown", "marker": None}
