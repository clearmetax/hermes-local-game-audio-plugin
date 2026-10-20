"""Procedural placeholder music and ambience (offline, deterministic, not AI).

Music: diatonic chord progression in the requested key/mode at the exact
requested BPM, with bass, arpeggio/lead, optional pad and drums, rendered in
whole bars so it loops perfectly. Ambience: slowly modulated filtered noise
beds with optional hum and wind layers.
"""

from __future__ import annotations

import math
import random
import re
from array import array

NOTE = {"c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4, "f": 5, "f#": 6, "gb": 6, "g": 7,
        "g#": 8, "ab": 8, "a": 9, "a#": 10, "bb": 10, "b": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]


def parse_key(key: str | None, prompt: str) -> tuple[int, bool]:
    text = (key or "").strip().lower()
    m = re.match(r"^([a-g](?:#|b)?)\s*(m|min|minor|maj|major)?$", text)
    if not m:
        m2 = re.search(r"\b([a-g](?:#|b)?)\s+(minor|major)\b", prompt.lower())
        if m2:
            return NOTE[m2.group(1)], m2.group(2) == "minor"
        sad = any(w in prompt.lower() for w in ("sad", "dark", "tense", "boss", "horror", "mysterious", "battle", "minor"))
        return (9 if sad else 0), sad
    minor = m.group(2) in ("m", "min", "minor")
    return NOTE[m.group(1)], minor


def _osc(kind: str, phase: float) -> float:
    p = phase % 1.0
    if kind == "square":
        return 1.0 if p < 0.5 else -1.0
    if kind == "pulse":
        return 1.0 if p < 0.25 else -1.0
    if kind == "saw":
        return 2 * p - 1
    if kind == "tri":
        return 4 * p - 1 if p < 0.5 else 3 - 4 * p
    return math.sin(2 * math.pi * p)


def _note(buf: array, start: int, length: int, freq: float, amp: float, kind: str, rate: int,
          attack: float = 0.005, release: float = 0.05, vib: float = 0.0) -> None:
    n = len(buf)
    a = max(1, int(attack * rate))
    r = max(1, int(release * rate))
    ph = 0.0
    inc = freq / rate
    for i in range(length + r):
        j = start + i
        if j >= n:
            break
        if i < a:
            env = i / a
        elif i < length:
            env = 1.0 - 0.3 * (i - a) / max(1, length - a)
        else:
            env = 0.7 * (1 - (i - length) / r)
        f = inc * (1 + vib * math.sin(2 * math.pi * 5.5 * i / rate)) if vib else inc
        ph += f
        buf[j] += amp * env * _osc(kind, ph)


def _drum(buf: array, start: int, kind: str, amp: float, rate: int, rnd: random.Random) -> None:
    n = len(buf)
    if kind == "kick":
        length = int(0.18 * rate)
        ph = 0.0
        for i in range(length):
            if start + i >= n:
                break
            f = 50 + 110 * math.exp(-i / (0.03 * rate))
            ph += f / rate
            buf[start + i] += amp * math.sin(2 * math.pi * ph) * math.exp(-i / (0.06 * rate))
    elif kind == "snare":
        length = int(0.15 * rate)
        for i in range(length):
            if start + i >= n:
                break
            e = math.exp(-i / (0.04 * rate))
            buf[start + i] += amp * e * (0.7 * rnd.uniform(-1, 1) + 0.3 * math.sin(2 * math.pi * 190 * i / rate))
    else:  # hat
        length = int(0.04 * rate)
        prev = 0.0
        for i in range(length):
            if start + i >= n:
                break
            w = rnd.uniform(-1, 1)
            hp = w - prev  # crude high-pass
            prev = w
            buf[start + i] += amp * 0.5 * hp * math.exp(-i / (0.008 * rate))


def synth_music(prompt: str, seconds: float, seed: int, params: dict, rate: int) -> list[array]:
    rnd = random.Random(seed)
    low = prompt.lower()
    bpm = float(params.get("bpm") or (140 if any(w in low for w in ("battle", "combat", "boss", "chase", "action", "fast")) else
                                      (80 if any(w in low for w in ("calm", "ambient", "peaceful", "sad", "slow", "lofi", "lo-fi")) else 112)))
    beats_per_bar = int(params.get("beats_per_bar") or 4)
    root, minor = parse_key(params.get("key"), prompt)
    scale = MINOR if minor else MAJOR
    calm = any(w in low for w in ("calm", "ambient", "peaceful", "gentle", "sleep", "menu", "soft"))
    intense = any(w in low for w in ("battle", "combat", "boss", "intense", "action", "chase"))
    chip = any(w in low for w in ("8-bit", "8bit", "chiptune", "retro", "nes", "pixel", "arcade"))
    lead_kind = "pulse" if chip else ("tri" if calm else "saw")
    beat = 60.0 / bpm
    total = int(seconds * rate)
    left = array("d", bytes(8 * total))
    right = array("d", bytes(8 * total))
    prog = [0, 5, 3, 4] if minor else [0, 4, 5, 3]  # i-VI-iv-v / I-V-vi-IV (scale degrees)
    if rnd.random() < 0.5:
        prog = [0, 3, 4, 0] if not minor else [0, 6, 5, 4]
    base_midi = 48 + root

    def freq(midi: float) -> float:
        return 440.0 * 2 ** ((midi - 69) / 12)

    def chord(deg: int) -> list[int]:
        return [base_midi + scale[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4)]

    melody_rhythm = [rnd.choice([0.5, 0.5, 1.0, 0.25]) for _ in range(16)]
    bar = 0
    t = 0.0
    while int(t * rate) < total:
        deg = prog[bar % len(prog)]
        notes = chord(deg)
        bar_start = int(t * rate)
        bar_len = int(beats_per_bar * beat * rate)
        # bass
        step = beat / (1 if calm else 2)
        k = 0
        while k * step < beats_per_bar * beat - 1e-9:
            _note(left, bar_start + int(k * step * rate), int(step * rate * 0.85), freq(notes[0] - 12),
                  0.28 if not calm else 0.22, "tri", rate, release=0.03)
            k += 1
        # pad
        if not chip:
            for m in notes:
                _note(right, bar_start, bar_len, freq(m + 12), 0.06, "sin", rate, attack=0.2, release=0.3)
                _note(left, bar_start, bar_len, freq(m + 12) * 1.003, 0.06, "sin", rate, attack=0.2, release=0.3)
        # arpeggio / lead
        pos = 0.0
        idx = 0
        while pos < beats_per_bar - 1e-9:
            dur = melody_rhythm[(idx + bar * 3) % len(melody_rhythm)]
            dur = min(dur, beats_per_bar - pos)
            if calm:
                pitch = notes[idx % 3] + 12
            else:
                pitch = rnd.choice(notes) + 12 + (12 if rnd.random() < 0.3 else 0)
            if rnd.random() > 0.15:
                s0 = bar_start + int(pos * beat * rate)
                amp = 0.16 if chip else 0.12
                _note(right, s0, int(dur * beat * rate * 0.9), freq(pitch), amp, lead_kind, rate, release=0.06, vib=0.004)
                _note(left, s0, int(dur * beat * rate * 0.9), freq(pitch), amp * 0.6, lead_kind, rate, release=0.06)
            pos += dur
            idx += 1
        # drums
        if not calm:
            for b in range(beats_per_bar):
                bs = bar_start + int(b * beat * rate)
                if b % 2 == 0:
                    _drum(left, bs, "kick", 0.5, rate, rnd)
                    _drum(right, bs, "kick", 0.5, rate, rnd)
                else:
                    _drum(left, bs, "snare", 0.25, rate, rnd)
                    _drum(right, bs, "snare", 0.25, rate, rnd)
                for h in range(4 if intense else 2):
                    hs = bs + int(h * beat / (4 if intense else 2) * rate)
                    _drum(right, hs, "hat", 0.18, rate, rnd)
                    _drum(left, hs, "hat", 0.12, rate, rnd)
        bar += 1
        t += beats_per_bar * beat
    pk = max(max(left), -min(left), max(right), -min(right)) or 1.0
    g = 0.8 / pk
    for i in range(total):
        left[i] *= g
        right[i] *= g
    return [left, right]


def synth_ambience(prompt: str, seconds: float, seed: int, rate: int) -> list[array]:
    rnd = random.Random(seed)
    low = prompt.lower()
    total = int(seconds * rate)
    out = []
    hum = any(w in low for w in ("hum", "machine", "facility", "engine", "industrial", "spaceship", "reactor"))
    water = any(w in low for w in ("rain", "water", "river", "ocean", "sea", "stream"))
    for ch in range(2):
        buf = array("d", bytes(8 * total))
        lp = lp2 = 0.0
        ph = rnd.random()
        for i in range(total):
            t = i / rate
            w = rnd.uniform(-1, 1)
            mod = 0.5 + 0.5 * math.sin(2 * math.pi * (0.07 + 0.01 * ch) * t + ph * 6)
            cutoff = (0.25 if water else 0.03) + (0.05 if water else 0.04) * mod
            lp += cutoff * (w - lp)
            lp2 += 0.5 * (lp - lp2)
            v = lp2 * (0.6 + 0.4 * mod) * (2.5 if not water else 1.2)
            if hum:
                v += 0.08 * math.sin(2 * math.pi * 60 * t) + 0.04 * math.sin(2 * math.pi * 120 * t + ch)
            buf[i] = v
        out.append(buf)
    pk = max(max(max(c), -min(c)) for c in out) or 1.0
    for c in out:
        for i in range(total):
            c[i] *= 0.7 / pk
    return out
