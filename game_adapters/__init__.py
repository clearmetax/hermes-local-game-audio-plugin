"""Game-engine adapters: detection, inventory, asset placement, integration."""

from .base import EngineAdapter
from .detect import ADAPTERS, detect_engine
from .godot import GodotAdapter, find_godot

__all__ = ["EngineAdapter", "ADAPTERS", "detect_engine", "GodotAdapter", "find_godot"]
