"""Lightweight adapters: detection + conventional asset folders only."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import EngineAdapter
from .scan import read_text

WEB_ENGINES = ("phaser", "pixi.js", "@pixi/", "three", "kaboom", "kaplay", "excalibur", "@babylonjs/", "babylonjs", "playcanvas", "howler")


class WebAdapter(EngineAdapter):
    name = "web"
    display_name = "Web game (JS/TS)"

    def __init__(self, root: Path):
        super().__init__(root)
        self.default_audio_dir = "public/assets/audio/sfx" if (root / "public").is_dir() else "assets/audio/sfx"

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        pkg = root / "package.json"
        if not pkg.is_file():
            return None
        try:
            data = json.loads(pkg.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return None
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        hits = sorted({d for d in deps for e in WEB_ENGINES if d == e or d.startswith(e)})
        if not hits:
            return None
        return {"engine": "web", "frameworks": hits, "marker": "package.json"}


class DefoldAdapter(EngineAdapter):
    name = "defold"
    display_name = "Defold"
    default_audio_dir = "assets/sounds"

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        return {"engine": "defold", "marker": "game.project"} if (root / "game.project").is_file() else None


class BevyAdapter(EngineAdapter):
    name = "bevy"
    display_name = "Bevy (Rust)"
    default_audio_dir = "assets/audio/sfx"

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        cargo = root / "Cargo.toml"
        if cargo.is_file() and re.search(r"^\s*bevy\s*=", read_text(cargo) or "", re.M):
            return {"engine": "bevy", "marker": "Cargo.toml"}
        return None


class LoveAdapter(EngineAdapter):
    name = "love2d"
    display_name = "LÖVE (Lua)"
    default_audio_dir = "assets/sfx"

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        if (root / "main.lua").is_file() and "love." in (read_text(root / "main.lua") or ""):
            return {"engine": "love2d", "marker": "main.lua"}
        return None


class PygameAdapter(EngineAdapter):
    name = "pygame"
    display_name = "Pygame"
    default_audio_dir = "assets/sfx"

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        for f in ("requirements.txt", "pyproject.toml"):
            if "pygame" in (read_text(root / f) or "").lower():
                return {"engine": "pygame", "marker": f}
        for p in sorted(root.glob("*.py"))[:20]:
            if re.search(r"^\s*import pygame", read_text(p) or "", re.M):
                return {"engine": "pygame", "marker": p.name}
        return None


class GameMakerAdapter(EngineAdapter):
    name = "gamemaker"
    display_name = "GameMaker"
    default_audio_dir = "datafiles/sfx"

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        yyp = sorted(root.glob("*.yyp"))
        return {"engine": "gamemaker", "marker": yyp[0].name} if yyp else None
