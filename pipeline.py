"""Generation pipeline: prompt -> N candidates -> validate -> analyse -> master ->
re-analyse -> (optional CLAP prompt match) -> rank -> publish the best.

Everything a tool returns about quality comes from here, so SFX, music,
ambience and stingers share one honest code path.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import analysis, dsp, mastering, safety, semantic, wav
from . import errors as E
from .audio_backend import MUSIC_KINDS, GenerationRequest, make_backend
from .settings import Settings

MAX_PROMPT_CHARS = 1000
_GENERATION_SLOT = threading.BoundedSemaphore(1)  # local inference is serial

VARIATION_HINTS = [
    "", "slightly brighter and sharper", "slightly deeper and heavier", "shorter and snappier",
    "with a little more texture", "softer and rounder", "a bit more distant", "more aggressive",
]
NEGATIVE_DEFAULTS = {
    "sfx": "music, melody, speech, voice, background noise, hiss, distortion, low quality, muffled",
    "ui": "music, melody, speech, voice, reverb tail, noise, distortion, low quality",
    "music": "vocals, singing, speech, distortion, noise, hiss, low quality, muffled, out of tune, off-beat",
    "stinger": "vocals, speech, distortion, noise, low quality, muffled, out of tune",
    "ambience": "music, melody, speech, voice, sudden loud events, distortion, low quality",
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ prompts
def _clean(s: str | None) -> str:
    return " ".join((s or "").split())


def build_sfx_prompt(description: str, style: str | None = None, hint: str = "", kind: str = "sfx") -> str:
    d = _clean(description)
    if not d:
        raise E.AudioPluginError(E.INVALID_INPUT, "description must not be empty")
    parts = [d]
    if style:
        parts.append(f"{_clean(style)} style")
    if hint:
        parts.append(hint)
    low = d.lower()
    wants_music = any(w in low for w in ("music", "melody", "jingle", "song", "soundtrack", "theme"))
    wants_voice = any(w in low for w in ("voice", "vocal", "speech", "grunt", "scream", "shout", "laugh"))
    if kind == "ambience":
        tail = ["continuous ambient soundscape", "stable background bed", "no sudden loud events", "suitable for looping"]
        if not wants_music:
            tail.append("no music")
    else:
        tail = ["isolated game sound effect", "clean", "close and dry" if kind == "ui" else "high quality"]
        if kind == "ui":
            tail.append("very short")
        if not wants_music:
            tail.append("no music")
    if not wants_voice:
        tail.append("no voice")
    prompt = ", ".join(parts + tail)
    if len(prompt) > MAX_PROMPT_CHARS:
        raise E.AudioPluginError(E.INVALID_INPUT, f"description is too long (max {MAX_PROMPT_CHARS} characters with style)")
    return prompt


# Back-compat name used by older callers/tests.
def build_prompt(description: str, style: str | None = None, hint: str = "") -> str:
    return build_sfx_prompt(description, style, hint)


def build_music_prompt(
    description: str, *, kind: str = "music", genre: str | None = None, mood: str | None = None,
    instruments: str | list[str] | None = None, bpm: float | None = None, key: str | None = None,
    energy: str | None = None, structure: str | None = None, loop: bool = False, vocals: bool = False,
    hint: str = "",
) -> str:
    """Stable-Audio-style descriptive tags: genre, character, instrumentation, tempo, key, form."""
    parts: list[str] = []
    if genre:
        parts.append(_clean(genre))
    d = _clean(description)
    if d:
        parts.append(d)
    if mood:
        parts.append(f"{_clean(mood)} mood")
    if instruments:
        inst = ", ".join(_clean(i) for i in instruments) if isinstance(instruments, list) else _clean(instruments)
        parts.append(f"featuring {inst}")
    if energy:
        parts.append(f"{_clean(energy)} energy")
    if bpm:
        parts.append(f"{int(round(bpm))} BPM")
    if key:
        parts.append(f"in {_clean(key)}")
    if structure:
        parts.append(_clean(structure))
    if hint:
        parts.append(hint)
    if kind == "stinger":
        parts += ["short musical sting", "clear decisive ending", "final note rings out"]
    elif loop:
        parts += ["seamless loop", "steady consistent energy throughout", "no intro", "no ending", "no fade out"]
    else:
        parts += ["clear intro and satisfying ending"]
    parts.append("video game soundtrack")
    if not vocals:
        parts.append("instrumental, no vocals")
    parts += ["high quality", "well mixed", "stereo"]
    prompt = ", ".join(p for p in parts if p)
    if not d and not genre:
        raise E.AudioPluginError(E.INVALID_INPUT, "give a description or a genre")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise E.AudioPluginError(E.INVALID_INPUT, f"music description is too long (max {MAX_PROMPT_CHARS} characters)")
    return prompt


# ------------------------------------------------------------------ spec
@dataclass
class Spec:
    kind: str
    description: str  # what the user asked for (used for CLAP)
    prompt: str
    seconds: float  # requested usable length
    gen_seconds: float  # length asked of the model
    master: mastering.MasterOptions
    bpm: float | None = None
    params: dict[str, Any] = field(default_factory=dict)
    candidates: int = 1
    max_attempts: int = 1
    min_score: float | None = None
    seed: int | None = None
    output_format: str = "wav"
    keep_alternates: bool = False


def loop_generation_seconds(seconds: float, bpm: float | None, beats_per_bar: int, crossfade: float, max_s: float) -> tuple[float, float]:
    """(loop length, seconds to generate): bar-snapped loop + crossfade tail + margin."""
    loop_len = seconds
    if bpm:
        bar = beats_per_bar * 60.0 / bpm
        loop_len = max(1, round(seconds / bar)) * bar
    gen = min(max_s, loop_len + crossfade + 1.0)
    if gen < loop_len + crossfade + 0.2:
        loop_len = max(1.0, gen - crossfade - 0.3)
        if bpm:
            bar = beats_per_bar * 60.0 / bpm
            loop_len = max(1, int(loop_len // bar)) * bar
    return loop_len, gen


# ------------------------------------------------------------------ generator
class Generator:
    """Holds one backend session (and the global generation slot) for a tool call."""

    def __init__(self, settings: Settings, backend_name: str | None, call_mcp: Callable[..., dict] | None, kind: str):
        self.settings = settings
        self.backend = make_backend(settings, override=backend_name, call_mcp=call_mcp, kind=kind)
        self._ctx = None
        self.session = None

    def __enter__(self) -> "Generator":
        if not _GENERATION_SLOT.acquire(timeout=self.settings.timeout):
            raise E.AudioPluginError(E.BACKEND_ERROR, "Another generation is still running; try again shortly")
        try:
            self._ctx = self.backend.session()
            self.session = self._ctx.__enter__()
        except BaseException:
            _GENERATION_SLOT.release()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._ctx is not None:
                self._ctx.__exit__(*exc)
        finally:
            _GENERATION_SLOT.release()

    # -------------------------------------------------------------- one take
    def _render(self, spec: Spec, attempt: int, workdir: Path) -> dict[str, Any]:
        s = self.settings
        s.renders_dir.mkdir(parents=True, exist_ok=True)
        safety.ensure_free_space(s.renders_dir)
        render = s.renders_dir / f"render-{uuid.uuid4().hex}.wav"
        params = dict(spec.params)
        params["kind"] = spec.kind
        if spec.bpm:
            params["bpm"] = spec.bpm
        if self.backend.supports_for(spec.kind).get("negative_prompt") and "negative_prompt" not in params:
            params["negative_prompt"] = NEGATIVE_DEFAULTS.get(spec.kind)
        seed = None if spec.seed is None else spec.seed + attempt
        t0 = time.monotonic()
        try:
            produced = self.session.generate(GenerationRequest(spec.prompt, spec.gen_seconds, render, seed=seed, params=params))
            elapsed = time.monotonic() - t0
            raw = Path(produced).read_bytes()
        finally:
            render.unlink(missing_ok=True)
        tolerance = 1.6 if spec.kind in MUSIC_KINDS else 1.5
        wav.validate(raw, expected_seconds=spec.gen_seconds, max_seconds=max(spec.gen_seconds * tolerance + 1, 5))
        audio = wav.decode(raw)
        pre = analysis.analyze(audio, kind=spec.kind, expected_seconds=spec.seconds, bpm=spec.bpm)
        try:
            mastered, steps, loop_info = mastering.master(audio, spec.master)
        except ValueError as exc:
            raise E.AudioPluginError(E.INVALID_OUTPUT, f"Could not build a loop from this take: {exc}") from exc
        post = analysis.analyze(mastered, kind=spec.kind, expected_seconds=spec.seconds, bpm=spec.bpm,
                                loop=spec.master.loop, loop_info=loop_info)
        data = wav.encode(mastered, loop=spec.master.loop) if (spec.master.enabled or spec.master.loop) else raw
        cand = workdir / f"take_{attempt + 1:02d}.wav"
        cand.write_bytes(data)
        return {"attempt": attempt + 1, "file": cand, "data": data, "frames": mastered.frames,
                "generation_seconds": round(elapsed, 2), "pre": pre, "post": post, "steps": steps, "loop": loop_info,
                "backend_audio": {"sample_rate": audio.rate, "channels": audio.n_channels, "seconds": round(audio.seconds, 3)}}

    def produce(self, spec: Spec, *, directory: Path, stem: str, root: Path | None, on_conflict: str = "rename",
                ffmpeg: str | None = None) -> dict[str, Any]:
        s = self.settings
        batch = s.candidates_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        batch.mkdir(parents=True, exist_ok=True)
        takes: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        state = {"attempt": 0, "fatal": False}

        def try_one() -> None:
            i = state["attempt"]
            state["attempt"] += 1
            try:
                t = self._render(spec, i, batch)
                t["technical"] = round((t["pre"]["rank_score"] + t["post"]["rank_score"]) / 2, 1)
                t["total"] = t["technical"]
                takes.append(t)
            except E.AudioPluginError as exc:
                failures.append({"take": i + 1, **exc.to_dict()["error"]})
                if exc.code in (E.BACKEND_NOT_CONFIGURED, E.BACKEND_NOT_READY, E.OUT_OF_SPACE, E.PATH_REJECTED):
                    state["fatal"] = True

        while state["attempt"] < spec.candidates and not state["fatal"]:
            try_one()
        self._semantic(spec, takes)  # one model load for the whole batch
        # Quality gate: keep trying (up to max_attempts) until a take reaches min_score.
        while (spec.min_score is not None and not state["fatal"] and state["attempt"] < spec.max_attempts
               and (not takes or max(t["total"] for t in takes) < spec.min_score)):
            try_one()
            self._semantic(spec, takes)
        if not takes:
            f0 = failures[0] if failures else {"code": E.BACKEND_ERROR, "message": "generation failed"}
            raise E.AudioPluginError(f0.get("code", E.BACKEND_ERROR), f0.get("message", "generation failed"),
                                     hint=f0.get("hint"), failures=failures)
        takes.sort(key=lambda t: -t["total"])
        best = takes[0]
        published = self._publish(spec, best, directory=directory, stem=stem, root=root, on_conflict=on_conflict, ffmpeg=ffmpeg)
        alternates = []
        for t in takes[1:]:
            alt = {"take": t["attempt"], "score": t["total"], "file": str(t["file"])}
            if spec.keep_alternates:
                alt.update(self._publish(spec, t, directory=directory, stem=f"{stem}_alt{t['attempt']}", root=root,
                                         on_conflict="rename", ffmpeg=ffmpeg))
            alternates.append(alt)
        post = best["post"]
        result = {
            **published,
            "kind": spec.kind,
            "backend": self.backend.name,
            "is_ai_model": self.backend.is_ai_model,
            "prompt_used": spec.prompt,
            "requested_seconds": spec.seconds,
            "generated_seconds": spec.gen_seconds,
            "generation_seconds": best["generation_seconds"],
            "quality": {
                "score": post["score"],
                "verdict": post["verdict"],
                "ranking_score": best["total"],
                "semantic": best.get("semantic"),
                "issues": [{k: v for k, v in i.items() if k != "penalty"} for i in post["issues"]],
                "issues_fixed_by_mastering": sorted({i["code"] for i in best["pre"]["issues"] if i["fixable_by_mastering"]}
                                                    - {i["code"] for i in post["issues"]}),
                "metrics": post["metrics"],
            },
            "mastering": best["steps"],
            "loop": best["loop"] or None,
            "candidates": {
                "generated": len(takes),
                "failed": len(failures),
                "chosen_take": best["attempt"],
                "scores": [{"take": t["attempt"], "total": t["total"], "technical": t["technical"],
                            "semantic": (t.get("semantic") or {}).get("points"), "verdict": t["post"]["verdict"]} for t in takes],
                "alternates": alternates,
                "folder": str(batch),
            },
            "failures": failures,
            "backend_capabilities": self.backend.supports_for(spec.kind),
        }
        caps = result["backend_capabilities"]
        notes = []
        if spec.bpm and not caps.get("bpm"):
            measured = post["metrics"].get("estimated_bpm")
            notes.append(f"{self.backend.name} cannot force a tempo; {spec.bpm:g} BPM was requested in the prompt"
                         + (f" and ~{measured:g} BPM was measured" if measured else "") + ".")
        if spec.params.get("key") and not caps.get("key"):
            notes.append(f"Key '{spec.params['key']}' was requested in the prompt only (not enforced by {self.backend.name}).")
        if spec.params.get("lyrics") and not caps.get("lyrics"):
            notes.append(f"{self.backend.name} does not support lyrics; they were ignored.")
        if not self.backend.is_ai_model:
            notes.append("Made with the procedural placeholder synth, not an AI model.")
        if not (best.get("semantic") or {}).get("points") and not self._clap_python():
            notes.append("Prompt-match (CLAP) scoring is off; ranking used technical analysis only.")
        result["notes"] = notes
        return result

    def _clap_python(self) -> str | None:
        return semantic.find_python(self.settings.values.get("clap_python", ""))

    def _semantic(self, spec: Spec, takes: list[dict[str, Any]]) -> None:
        todo = [t for t in takes if "semantic" not in t]
        if not todo:
            return
        py = self._clap_python()
        if not py:
            for t in todo:
                t["semantic"] = None
            return
        res = semantic.score([t["file"] for t in todo], spec.description or spec.prompt, spec.kind,
                             python=py, model=self.settings.values.get("clap_model") or semantic.DEFAULT_MODEL)
        if not res.get("available"):
            for t in todo:
                t["semantic"] = {"available": False, "reason": res.get("reason")}
            return
        for t, sc in zip(todo, res["scores"]):
            pts = semantic.semantic_points(sc["cosine"])
            t["semantic"] = {"available": True, "model": res["model"], "cosine": sc["cosine"],
                             "prompt_probability": sc["prompt_probability"], "points": round(pts, 1)}
            t["total"] = round(0.55 * t["technical"] + 0.45 * pts, 1)

    def _publish(self, spec: Spec, take: dict[str, Any], *, directory: Path, stem: str, root: Path | None,
                 on_conflict: str, ffmpeg: str | None) -> dict[str, Any]:
        fmt = spec.output_format
        ext = ".ogg" if fmt == "ogg" else ".wav"
        if fmt == "ogg" and not ffmpeg:
            fmt, ext = "wav", ".wav"
            fallback = "ffmpeg not found; saved WAV instead of OGG"
        else:
            fallback = None
        target = directory / f"{stem}{ext}"
        renamed_from = None
        if target.exists() or target.is_symlink():
            if on_conflict == "error":
                raise E.AudioPluginError(E.FILE_EXISTS, f"{target.name} already exists; choose another filename", existing=str(target))
            renamed_from = target.name
            target = safety.unique_path(directory, stem, ext)
        if root is not None:
            target = safety.resolve_inside(root, target.relative_to(root).as_posix())
        if fmt == "ogg":
            safety.ensure_free_space(target.parent)
            from .codecs_io import encode_ogg

            encode_ogg(take["data"], target, ffmpeg, loop_frames=take["frames"] if spec.master.loop else None)
        else:
            safety.publish_exclusive(take["data"], target)
        out = {"path": str(target), "format": fmt, "audio": wav.inspect_bytes(take["data"]).to_dict()}
        if wav.read_loop(take["data"]):
            out["embedded_loop"] = True
        if renamed_from:
            out["renamed_from"] = renamed_from
        if fallback:
            out["format_note"] = fallback
        return out


# ------------------------------------------------------------------ manifests
def project_key(root: Path) -> str:
    return hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:16]


def append_manifest(settings: Settings, root: Path | None, entry: dict[str, Any]) -> Path:
    settings.manifests_dir.mkdir(parents=True, exist_ok=True)
    path = settings.manifests_dir / (f"{project_key(root)}.jsonl" if root else "library.jsonl")
    slim = {k: v for k, v in entry.items() if k not in ("quality",)}
    if "quality" in entry:
        slim["quality_score"] = entry["quality"].get("score")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(slim, ensure_ascii=False, default=str) + "\n")
    return path


def is_music_word(text: str) -> bool:
    return bool(re.search(r"\b(music|soundtrack|theme|song|track|bgm|loop music|score)\b", text or "", re.I))


__all__ = ["Spec", "Generator", "build_sfx_prompt", "build_music_prompt", "build_prompt", "loop_generation_seconds",
           "append_manifest", "project_key", "now", "dsp", "VARIATION_HINTS"]
