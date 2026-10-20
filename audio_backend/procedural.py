"""Built-in procedural synthesizer (opt-in, offline, NOT an AI model).

Useful for instant placeholder SFX on machines that cannot run Stable Audio,
for CI, and for the demo project. It maps keywords in the description to a
small set of classic retro-game recipes (sfxr-style). Results are always
labelled ``backend: procedural`` so nobody mistakes them for model output.
"""

from __future__ import annotations

import hashlib
import math
import random
from array import array
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import errors as E
from ..wav import encode_pcm16
from .base import AudioBackend, BackendStatus, GenerationRequest, Session
from .procedural_music import synth_ambience, synth_music

RATE = 44100

RECIPES = {
    "explosion": ("explosion", "explode", "blast", "boom", "grenade", "bomb", "detonat"),
    "laser": ("laser", "zap", "plasma", "blaster", "shoot", "shot", "fire", "pew", "beam"),
    "hit": ("hit", "impact", "punch", "smack", "clang", "strike", "thud", "slash", "sword", "damage"),
    "hurt": ("hurt", "pain", "ouch", "injur"),
    "jump": ("jump", "hop", "bounce", "spring"),
    "pickup": ("pickup", "pick up", "coin", "collect", "gem", "item", "loot"),
    "powerup": ("power", "level up", "levelup", "upgrade", "heal", "buff", "victory", "win", "success"),
    "ui": ("click", "ui", "button", "menu", "select", "confirm", "tick", "blip", "beep"),
    "whoosh": ("whoosh", "swing", "swoosh", "dash", "woosh", "wind"),
    "footstep": ("footstep", "step", "walk", "run"),
    "alarm": ("alarm", "warning", "siren", "alert"),
    "lose": ("lose", "game over", "fail", "death", "die", "defeat"),
}


def pick_recipe(prompt: str) -> str:
    low = prompt.lower()
    best, best_pos = "blip", len(low) + 1
    for recipe, words in RECIPES.items():
        for w in words:
            pos = low.find(w)
            if pos != -1 and pos < best_pos:
                best, best_pos = recipe, pos
    return best


def _env(i: int, n: int, attack: float = 0.005, curve: float = 3.0) -> float:
    t = i / RATE
    a = attack
    if t < a:
        return t / a
    x = (i - a * RATE) / max(1.0, n - a * RATE)
    return max(0.0, (1 - x)) ** curve


def synth(prompt: str, seconds: float, seed: int) -> array:
    rnd = random.Random(seed)
    recipe = pick_recipe(prompt)
    n = max(1, int(seconds * RATE))
    out = array("d", bytes(8 * n))
    phase = 0.0
    lp = 0.0
    j = lambda lo, hi: rnd.uniform(lo, hi)  # noqa: E731
    if recipe == "explosion":
        cutoff = j(0.05, 0.12)
        for i in range(n):
            lp += cutoff * (rnd.uniform(-1, 1) - lp)
            out[i] = 2.2 * lp * _env(i, n, 0.002, 2.2)
    elif recipe == "laser":
        f0, f1 = j(900, 1600), j(120, 300)
        for i in range(n):
            f = f0 + (f1 - f0) * (i / n) ** 0.6
            phase += 2 * math.pi * f / RATE
            sq = 1.0 if math.sin(phase) >= 0 else -1.0
            out[i] = 0.6 * sq * _env(i, n, 0.001, 1.6)
    elif recipe in ("hit", "hurt"):
        f0 = j(140, 220) if recipe == "hit" else j(350, 500)
        for i in range(n):
            phase += 2 * math.pi * f0 * (1 - 0.6 * i / n) / RATE
            noise = rnd.uniform(-1, 1) * math.exp(-i / (RATE * 0.02))
            tone = math.sin(phase) * _env(i, n, 0.001, 4)
            ring = math.sin(2 * math.pi * f0 * 6.3 * i / RATE) * math.exp(-i / (RATE * 0.12)) * 0.25
            out[i] = 0.8 * noise + 0.7 * tone + (ring if recipe == "hit" else 0)
    elif recipe == "jump":
        f0, f1 = j(220, 320), j(600, 900)
        for i in range(n):
            phase += 2 * math.pi * (f0 + (f1 - f0) * i / n) / RATE
            out[i] = 0.55 * (1.0 if math.sin(phase) >= 0 else -1.0) * _env(i, n, 0.003, 1.2)
    elif recipe in ("pickup", "powerup", "lose"):
        if recipe == "pickup":
            notes = [j(880, 1000), j(1300, 1500)]
        elif recipe == "powerup":
            base = j(330, 440)
            notes = [base * r for r in (1, 1.25, 1.5, 2.0)]
        else:
            base = j(330, 400)
            notes = [base * r for r in (1, 0.84, 0.7, 0.5)]
        seg = n // len(notes)
        for i in range(n):
            f = notes[min(len(notes) - 1, i // max(1, seg))]
            phase += 2 * math.pi * f / RATE
            out[i] = 0.5 * (1.0 if math.sin(phase) >= 0 else -1.0) * _env(i, n, 0.002, 0.8)
    elif recipe == "ui":
        f = j(1200, 2000)
        for i in range(n):
            out[i] = 0.6 * math.sin(2 * math.pi * f * i / RATE) * math.exp(-i / (RATE * 0.015))
    elif recipe == "whoosh":
        for i in range(n):
            x = i / n
            cutoff = 0.02 + 0.25 * math.sin(math.pi * x)
            lp += cutoff * (rnd.uniform(-1, 1) - lp)
            out[i] = 1.6 * lp * math.sin(math.pi * x)
    elif recipe == "footstep":
        for i in range(n):
            lp += 0.15 * (rnd.uniform(-1, 1) - lp)
            out[i] = 1.5 * lp * math.exp(-i / (RATE * 0.03))
    elif recipe == "alarm":
        for i in range(n):
            f = 700 + 300 * (1 if (i // int(RATE * 0.25)) % 2 else 0)
            phase += 2 * math.pi * f / RATE
            out[i] = 0.45 * math.sin(phase)
    else:  # blip
        f = j(500, 900)
        for i in range(n):
            phase += 2 * math.pi * f / RATE
            out[i] = 0.5 * math.sin(phase) * _env(i, n, 0.002, 2)
    peak = max(max(out), -min(out)) or 1.0
    scale = 0.89 / peak
    for i in range(n):
        out[i] *= scale
    return out


class _ProcSession(Session):
    def generate(self, request: GenerationRequest) -> Path:
        if request.output_path.exists():
            raise E.AudioPluginError(E.FILE_EXISTS, f"Render path exists: {request.output_path}")
        seed = request.seed
        if seed is None:
            seed = random.SystemRandom().randrange(2**31)
        mixed = int(hashlib.sha256(f"{request.prompt}|{seed}".encode()).hexdigest()[:8], 16)
        kind = request.params.get("kind", "sfx")
        if kind in ("music", "stinger"):
            chans = synth_music(request.prompt, request.seconds, mixed, request.params, RATE)
        elif kind == "ambience":
            chans = synth_ambience(request.prompt, request.seconds, mixed, RATE)
        else:
            chans = [synth(request.prompt, request.seconds, mixed)]
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(request.output_path, "xb") as handle:
            handle.write(encode_pcm16(chans, RATE))
        return request.output_path


class ProceduralBackend(AudioBackend):
    name = "procedural"
    is_ai_model = False
    supports = {"sfx": True, "music": True, "ambience": True, "bpm": True, "key": True, "seed": True,
                "lyrics": False, "negative_prompt": False, "max_seconds": 300}

    def status(self, *, deep: bool = False) -> BackendStatus:
        return BackendStatus(
            self.name,
            configured=True,
            ready=True,
            is_ai_model=False,
            details={
                "description": "Built-in synthesizer for placeholder SFX, chiptune-style music (exact BPM/key/bars) and ambience beds. Not an AI model; quality is basic.",
                "supports": {"sfx": True, "music": True, "ambience": True, "bpm": True, "key": True, "seed": True, "lyrics": False},
                "recipes": sorted(RECIPES) + ["blip"],
                "sample_rate": RATE,
                "channels": 1,
            },
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        yield _ProcSession()
