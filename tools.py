"""Tool handlers. Every handler returns a JSON string and never raises."""

from __future__ import annotations

import difflib
import functools
import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import analysis, mastering, pipeline, safety, semantic, wav
from . import errors as E
from .audio_backend import make_backend, resolve_choice
from .audio_backend.diagnostics import machine_report
from .codecs_io import decode_any, find_ffmpeg
from .game_adapters import GodotAdapter, detect_engine, find_godot
from .game_adapters.scan import apply_style
from .pipeline import build_music_prompt, build_prompt, build_sfx_prompt  # noqa: F401 (re-exported)
from .settings import PLUGIN_NAME, Settings, load_settings

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = pipeline.MAX_PROMPT_CHARS
MAX_SOUNDS_PER_FEATURE = 12
SFX_KINDS = ("sfx", "ui", "ambience")
ALL_KINDS = ("sfx", "ui", "ambience", "music", "stinger")
_VARIATION_HINTS = pipeline.VARIATION_HINTS


class Runtime:
    """Bridges handlers to the Hermes context (settings, MCP) without globals."""

    def __init__(self, get_config: Callable[[str, Any], Any] | None = None, call_mcp: Callable[..., dict] | None = None):
        self.get_config = get_config
        self.call_mcp = call_mcp

    def settings(self) -> Settings:
        return load_settings(self.get_config)


def _respond(fn: Callable[..., dict[str, Any]]) -> Callable[..., str]:
    @functools.wraps(fn)
    def wrapper(rt: Runtime, args: dict[str, Any] | None = None, **kwargs: Any) -> str:
        try:
            if args is not None and not isinstance(args, dict):
                raise E.AudioPluginError(E.INVALID_INPUT, "arguments must be an object")
            result = fn(rt, dict(args or {}))
        except E.AudioPluginError as exc:
            result = exc.to_dict()
        except Exception as exc:  # never let an exception escape into Hermes
            logger.exception("%s failed", fn.__name__)
            result = E.AudioPluginError(E.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}").to_dict()
        return json.dumps(result, ensure_ascii=False, default=str)

    return wrapper


# ----------------------------------------------------------------- helpers
def _opt_str(args: dict[str, Any], key: str, max_len: int = 500) -> str | None:
    v = args.get(key)
    if v is None or v == "":
        return None
    if not isinstance(v, str) or "\0" in v:
        raise E.AudioPluginError(E.INVALID_INPUT, f"{key} must be a string")
    if len(v) > max_len:
        raise E.AudioPluginError(E.INVALID_INPUT, f"{key} is longer than {max_len} characters")
    return v.strip()


def _num(args: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    v = args.get(key, default)
    if v is None:
        v = default
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise E.AudioPluginError(E.INVALID_INPUT, f"{key} must be a number") from None
    v = float(v)
    if v != v or v < lo or v > hi:
        raise E.AudioPluginError(E.INVALID_INPUT, f"{key} must be between {lo:g} and {hi:g}")
    return v


def _opt_num(args: dict[str, Any], key: str, lo: float, hi: float) -> float | None:
    if args.get(key) is None:
        return None
    return _num(args, key, 0.0, lo, hi)


def _bool(args: dict[str, Any], key: str, default: bool) -> bool:
    v = args.get(key, default)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise E.AudioPluginError(E.INVALID_INPUT, f"{key} must be true or false")
    return v


def _seed(args: dict[str, Any]) -> int | None:
    seed = args.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise E.AudioPluginError(E.INVALID_INPUT, "seed must be a non-negative integer")
    return seed


def _kind(args: dict[str, Any], default: str, allowed: tuple[str, ...]) -> str:
    k = args.get("kind") or default
    if k not in allowed:
        raise E.AudioPluginError(E.INVALID_INPUT, f"kind must be one of {list(allowed)}")
    return k


def _format(s: Settings, args: dict[str, Any]) -> str:
    f = (args.get("output_format") or s.values["output_format"] or "wav").lower()
    if f not in ("wav", "ogg"):
        raise E.AudioPluginError(E.INVALID_INPUT, "output_format must be wav or ogg")
    return f


def _candidates(s: Settings, args: dict[str, Any], music: bool) -> tuple[int, int, float | None]:
    default = int(s.values["default_music_candidates" if music else "default_candidates"])
    n = int(_num(args, "candidates", max(1, min(default, s.max_candidates)), 1, s.max_candidates))
    min_score = _opt_num(args, "min_score", 0, 100)
    max_attempts = int(_num(args, "max_attempts", max(n, 2 * n if min_score else n), n, s.max_candidates * 2))
    return n, max_attempts, min_score


def _key(args: dict[str, Any]) -> str | None:
    k = _opt_str(args, "key", 20)
    if k and not re.fullmatch(r"[A-Ga-g][#b]?\s*(m|min|minor|maj|major)?", k):
        raise E.AudioPluginError(E.INVALID_INPUT, "key must look like 'C', 'F# minor', 'Bb major' or 'Am'")
    return k


def _resolve_output_dir(settings: Settings, args: dict[str, Any], root: Path | None, kind: str = "sfx", subdir_key: str = "subdir"):
    """Return (adapter|None, engine_info|None, directory, naming_style)."""
    if root is None:
        lib = settings.library_dir / ("music" if kind in ("music", "stinger") else ("ambience" if kind == "ambience" else "sfx"))
        lib.mkdir(parents=True, exist_ok=True)
        return None, None, lib, "snake_case"
    adapter, info = detect_engine(root)
    sub = _opt_str(args, subdir_key)
    if sub is None:
        sub, style = _conventional_dir(adapter, kind=kind)
    else:
        _, style = _conventional_dir(adapter, quick=True, kind=kind)
    directory = safety.resolve_inside(root, sub)
    safety.make_dirs_inside(root, directory)
    return adapter, info, directory, style


_KIND_FOLDERS = {"music": r"(music|bgm|soundtrack|ost|songs?)", "stinger": r"(music|bgm|stingers?|jingles?)",
                 "ambience": r"(ambien(ce|t)|amb|environment|atmos)", "sfx": r"(sfx|sounds?|se|effects)", "ui": r"(ui|sfx|sounds?)"}


def _default_dir_for(adapter, kind: str) -> str:  # noqa: ANN001
    base = adapter.default_audio_dir
    if kind in ("sfx", "ui"):
        return base
    word = "music" if kind in ("music", "stinger") else "ambience"
    m = re.search(r"(sfx|SFX|Sfx|sounds|Sounds)$", base)
    if not m:
        return f"{base}/{word}"
    repl = word.capitalize() if m.group(1)[0].isupper() else word
    return base[: m.start()] + repl


def _conventional_dir(adapter, quick: bool = False, kind: str = "sfx") -> tuple[str, str]:  # noqa: ANN001
    from .game_adapters.scan import AUDIO_EXTS, naming_style, walk

    w = walk(adapter.root, max_files=8000)
    audio = [p for p in w.files if p.suffix.lower() in AUDIO_EXTS]
    style = naming_style([p.stem for p in audio])
    if quick or not audio:
        return _default_dir_for(adapter, kind), style
    counts: dict[str, int] = {}
    for p in audio:
        d = p.parent.relative_to(adapter.root).as_posix()
        counts[d] = counts.get(d, 0) + 1
    pat = _KIND_FOLDERS.get(kind, _KIND_FOLDERS["sfx"])
    matching = [d for d in counts if re.search(rf"(^|/){pat}(/|$)", d, re.I)]
    if matching:
        return max(matching, key=lambda d: counts[d]), style
    if kind in ("sfx", "ui"):
        other = rf"(^|/)({_KIND_FOLDERS['music']}|{_KIND_FOLDERS['ambience']})(/|$)"
        generic = [d for d in counts if not re.search(other, d, re.I)]
        if not generic:
            return _default_dir_for(adapter, kind), style
        best = max(generic, key=lambda d: counts[d])
        return ("." if best == "." else best), style
    # No music/ambience folder yet: sibling of the existing audio folder.
    best = max(counts, key=lambda d: counts[d])
    parent = best.rsplit("/", 1)[0] if "/" in best else ""
    word = "music" if kind in ("music", "stinger") else "ambience"
    leaf = best.rsplit("/", 1)[-1]
    word = word.capitalize() if leaf[:1].isupper() else word
    return (f"{parent}/{word}" if parent else word), style


def _final_stem(raw: str, style: str) -> str:
    stem = safety.safe_stem(raw)
    styled = apply_style(stem, style)
    return styled if re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", styled) else stem


def _with_project_ref(result: dict[str, Any], adapter, root: Path | None) -> dict[str, Any]:  # noqa: ANN001
    if root is not None and adapter is not None:
        rel = Path(result["path"]).relative_to(root).as_posix()
        result["project_path"] = rel
        result["engine_ref"] = adapter.engine_ref(rel)
    return result


def _master_opts(kind: str, args: dict[str, Any], *, loop: bool, bpm: float | None, beats_per_bar: int,
                 loop_seconds: float | None) -> mastering.MasterOptions:
    raw = args.get("post_process", args.get("mastering"))
    if raw is not None and not isinstance(raw, (dict, bool)):
        raise E.AudioPluginError(E.INVALID_INPUT, "post_process must be an object or boolean")
    if isinstance(raw, dict):
        for k in ("target_lufs", "peak_dbtp", "ceiling_dbtp", "normalize_peak_db"):
            if raw.get(k) is not None:
                _num(raw, k, 0, -60, 0)
    o = mastering.MasterOptions.from_args(kind, raw, loop=loop, bpm=bpm, beats_per_bar=beats_per_bar, loop_seconds=loop_seconds)
    return o


def _check_loop_fits(loop_len: float, crossfade: float) -> None:
    if loop_len < 2 * crossfade:
        raise E.AudioPluginError(E.INVALID_INPUT, f"Loop length {loop_len:.2f}s must be at least twice the crossfade ({crossfade:.2f}s)",
                                 hint="Use a longer duration_seconds or a shorter crossfade_seconds.")


def _root(s: Settings, args: dict[str, Any], required: bool = False) -> Path | None:
    project = _opt_str(args, "project_path", 2000)
    if not project:
        if required:
            raise E.AudioPluginError(E.INVALID_INPUT, "project_path is required")
        return None
    return safety.resolve_project_root(project, s.values["allowed_project_roots"])


def _ffmpeg(s: Settings) -> str | None:
    return find_ffmpeg(s.values.get("ffmpeg_executable", ""))


# ------------------------------------------------------------------- status
@_respond
def audio_status(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    deep = bool(args.get("deep_check", False))
    v = s.values
    backends: dict[str, Any] = {}
    for name in ("agent_audio", "comfyui"):
        b = make_backend(s, override=name, call_mcp=rt.call_mcp)
        st = b.status(deep=deep).to_dict()
        st["capabilities"] = {"sfx": b.supports_for("sfx"), "music": b.supports_for("music")}
        backends[name] = st
    proc = make_backend(s, override="procedural")
    backends["procedural"] = {**proc.status().to_dict(), "capabilities": proc.supports,
                              "note": "Placeholder synth; opt-in via backend='procedural'."}
    sfx_choice = resolve_choice(s, None, "sfx")
    music_choice = resolve_choice(s, None, "music")
    clap = semantic.find_python(v["clap_python"])
    out: dict[str, Any] = {
        "success": True,
        "plugin": PLUGIN_NAME,
        "routing": {"sfx": sfx_choice, "music": music_choice, "setting_backend": v["backend"], "setting_music_backend": v["music_backend"]},
        "backends": backends,
        "ai_backend": backends[sfx_choice] if sfx_choice in backends else backends["agent_audio"],
        "procedural_backend": {"ready": True, "is_ai_model": False, "note": "Placeholder synth; opt-in via backend='procedural'."},
        "generation_ready": {"sfx": backends[sfx_choice]["ready"] if deep or sfx_choice == "procedural" else None,
                             "music": backends[music_choice]["ready"] if deep or music_choice == "procedural" else None},
        "quality_tools": {
            "analysis_and_mastering": "built in (BS.1770 loudness, true peak, limiter, loops, tempo, spectrum)",
            "clap_prompt_match": {"configured": bool(clap), "python": clap, "model": v["clap_model"]},
            "ffmpeg": _ffmpeg(s),
            "default_candidates": {"sfx": int(v["default_candidates"]), "music": int(v["default_music_candidates"])},
        },
        "limits": {
            "default_duration_seconds": s.default_duration,
            "max_duration_seconds": s.max_duration,
            "default_music_seconds": float(v["default_music_seconds"]),
            "max_music_seconds": s.max_music_duration,
            "timeout_seconds": s.timeout,
            "max_variations": s.max_variations,
            "max_candidates": s.max_candidates,
        },
        "paths": {"data_dir": str(s.data_dir), "library_dir": str(s.library_dir)},
        "machine": machine_report(s.data_dir),
        "godot_executable": find_godot(v["godot_executable"]),
        "offline": "All generation runs on this computer (Agent Audio, or ComfyUI on localhost). No cloud audio API is ever called.",
    }
    out["selected_backend"] = v["backend"]
    if not deep:
        out["readiness_note"] = "Not checked. Call audio_status with deep_check=true to verify the model runtimes."
    project = _opt_str(args, "project_path", 2000)
    if project:
        root = safety.resolve_project_root(project, v["allowed_project_roots"])
        adapter, info = detect_engine(root)
        out["project"] = {"root": str(root), **info, "default_audio_dir": adapter.default_audio_dir,
                          "default_music_dir": _default_dir_for(adapter, "music")}
    return out


# --------------------------------------------------------------- generation
def _sfx_spec(s: Settings, args: dict[str, Any], *, kind: str, desc: str, seconds: float, style: str | None,
              hint: str = "", music: bool = False) -> pipeline.Spec:
    loop = _bool(args, "loop", kind == "ambience")
    if loop and kind not in ("ambience", "music"):
        raise E.AudioPluginError(E.INVALID_INPUT, "loop is for ambience (use generate_music for music loops)")
    crossfade = _opt_num(args, "crossfade_seconds", 0.05, 8.0)
    gen_seconds = seconds
    loop_len = None
    if loop:
        xf = crossfade if crossfade is not None else min(1.5, max(0.05, seconds * 0.1))
        loop_len, gen_seconds = pipeline.loop_generation_seconds(seconds, None, 4, xf, s.max_duration + 5)
        _check_loop_fits(loop_len, xf)
    n, max_attempts, min_score = _candidates(s, args, music)
    mo = _master_opts(kind, args, loop=loop, bpm=None, beats_per_bar=4, loop_seconds=loop_len)
    if crossfade is not None:
        mo.crossfade_seconds = crossfade
    return pipeline.Spec(kind=kind, description=desc, prompt=build_sfx_prompt(desc, style, hint, kind), seconds=seconds,
                         gen_seconds=gen_seconds, master=mo, candidates=n, max_attempts=max_attempts, min_score=min_score,
                         seed=_seed(args), output_format=_format(s, args), keep_alternates=_bool(args, "keep_alternates", False))


@_respond
def generate_sound(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    desc = _opt_str(args, "description", MAX_PROMPT_CHARS)
    if not desc:
        raise E.AudioPluginError(E.INVALID_INPUT, "description is required")
    kind = _kind(args, "sfx", SFX_KINDS)
    seconds = _num(args, "duration_seconds", s.default_duration if kind != "ambience" else 20.0, 0.1, s.max_duration)
    root = _root(s, args)
    on_conflict = args.get("on_conflict") or "rename"
    if on_conflict not in ("rename", "error"):
        raise E.AudioPluginError(E.INVALID_INPUT, "on_conflict must be 'rename' or 'error'")
    spec = _sfx_spec(s, args, kind=kind, desc=desc, seconds=seconds, style=_opt_str(args, "style", 200))
    adapter, info, directory, naming = _resolve_output_dir(s, args, root, kind)
    stem = _final_stem(_opt_str(args, "filename", 120) or " ".join(desc.split()[:5]), naming)
    with pipeline.Generator(s, args.get("backend"), rt.call_mcp, kind) as gen:
        res = gen.produce(spec, directory=directory, stem=stem, root=root, on_conflict=on_conflict, ffmpeg=_ffmpeg(s))
    _with_project_ref(res, adapter, root)
    manifest = pipeline.append_manifest(s, root, {"time": pipeline.now(), "tool": "generate_sound", "description": desc, **res})
    res.update(success=True, engine=(info or {}).get("engine"), manifest=str(manifest),
               note="Generated, quality-checked and mastered. It is not connected to gameplay until integrated.")
    return res


@_respond
def generate_music(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    kind = _kind(args, "music", ("music", "stinger"))
    desc = _opt_str(args, "description", MAX_PROMPT_CHARS) or ""
    genre = _opt_str(args, "genre", 120)
    if not desc and not genre:
        raise E.AudioPluginError(E.INVALID_INPUT, "description or genre is required")
    default_len = float(s.values["default_music_seconds"]) if kind == "music" else 4.0
    seconds = _num(args, "duration_seconds", min(default_len, s.max_music_duration), 1.0, s.max_music_duration)
    bpm = _opt_num(args, "bpm", 40, 240)
    key = _key(args)
    beats = int(_num(args, "beats_per_bar", 4, 2, 7))
    loop = _bool(args, "loop", kind == "music")
    if kind == "stinger":
        loop = False
    vocals = _bool(args, "vocals", False)
    lyrics = _opt_str(args, "lyrics", 3000)
    if lyrics and not vocals:
        vocals = True
    instruments = args.get("instruments")
    if instruments is not None and not (isinstance(instruments, str) or (isinstance(instruments, list) and all(isinstance(i, str) for i in instruments))):
        raise E.AudioPluginError(E.INVALID_INPUT, "instruments must be a string or list of strings")
    crossfade = _opt_num(args, "crossfade_seconds", 0.05, 8.0)
    gen_seconds, loop_len = seconds, None
    if loop:
        xf = crossfade if crossfade is not None else min(0.75, max(0.05, seconds * 0.1))
        loop_len, gen_seconds = pipeline.loop_generation_seconds(seconds, bpm, beats, xf, s.max_music_duration)
        _check_loop_fits(loop_len, xf)
    prompt = build_music_prompt(desc, kind=kind, genre=genre, mood=_opt_str(args, "mood", 120), instruments=instruments,
                                bpm=bpm, key=key, energy=_opt_str(args, "energy", 60), structure=_opt_str(args, "structure", 300),
                                loop=loop, vocals=vocals)
    n, max_attempts, min_score = _candidates(s, args, True)
    mo = _master_opts(kind, args, loop=loop, bpm=bpm, beats_per_bar=beats, loop_seconds=loop_len)
    if crossfade is not None:
        mo.crossfade_seconds = crossfade
    spec = pipeline.Spec(kind=kind, description=", ".join(x for x in (genre, desc) if x), prompt=prompt, seconds=seconds,
                         gen_seconds=gen_seconds, master=mo, bpm=bpm,
                         params={"key": key, "beats_per_bar": beats, "lyrics": lyrics, "vocals": vocals},
                         candidates=n, max_attempts=max_attempts, min_score=min_score, seed=_seed(args),
                         output_format=_format(s, args), keep_alternates=_bool(args, "keep_alternates", False))
    root = _root(s, args)
    on_conflict = args.get("on_conflict") or "rename"
    if on_conflict not in ("rename", "error"):
        raise E.AudioPluginError(E.INVALID_INPUT, "on_conflict must be 'rename' or 'error'")
    adapter, info, directory, naming = _resolve_output_dir(s, args, root, kind)
    base_name = _opt_str(args, "filename", 120) or " ".join((desc or genre or "music").split()[:4]) + (" loop" if loop else "")
    stem = _final_stem(base_name, naming)
    with pipeline.Generator(s, args.get("backend"), rt.call_mcp, kind) as gen:
        res = gen.produce(spec, directory=directory, stem=stem, root=root, on_conflict=on_conflict, ffmpeg=_ffmpeg(s))
    _with_project_ref(res, adapter, root)
    if min_score is not None:
        res["quality_gate"] = {"min_score": min_score, "reached": res["quality"]["ranking_score"] >= min_score}
    manifest = pipeline.append_manifest(s, root, {"time": pipeline.now(), "tool": "generate_music", "description": spec.description, **res})
    res.update(success=True, engine=(info or {}).get("engine"), manifest=str(manifest),
               note=("Seamless loop with embedded loop points. " if res.get("embedded_loop") else "") +
                    "Use integrate_game_audio with mode='music' to start it in a scene.")
    return res


@_respond
def generate_variations(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    desc = _opt_str(args, "description", MAX_PROMPT_CHARS)
    if not desc:
        raise E.AudioPluginError(E.INVALID_INPUT, "description is required")
    kind = _kind(args, "sfx", ("sfx", "ui"))
    seconds = _num(args, "duration_seconds", s.default_duration, 0.1, s.max_duration)
    root = _root(s, args)
    count = int(_num(args, "count", 3, 1, s.max_variations))
    style = _opt_str(args, "style", 200)
    vary = args.get("vary_prompt", True) is not False
    adapter, info, directory, naming = _resolve_output_dir(s, args, root, kind)
    base = _final_stem(_opt_str(args, "filename", 120) or " ".join(desc.split()[:5]), naming)
    sep = "-" if naming == "kebab-case" else "_"
    takes, failures = [], []
    a2 = dict(args)
    a2.setdefault("candidates", 1)
    with pipeline.Generator(s, args.get("backend"), rt.call_mcp, kind) as gen:
        for i in range(count):
            hint = _VARIATION_HINTS[i % len(_VARIATION_HINTS)] if vary else ""
            spec = _sfx_spec(s, a2, kind=kind, desc=desc, seconds=seconds, style=style, hint=hint)
            if spec.seed is not None:
                spec.seed += i * 1000  # distinct takes even with a fixed seed
            try:
                res = gen.produce(spec, directory=directory, stem=f"{base}{sep}v{i + 1:02d}", root=root)
                takes.append(_with_project_ref(res, adapter, root))
            except E.AudioPluginError as exc:
                failures.append({"take": i + 1, **exc.to_dict()["error"]})
                if exc.code in (E.BACKEND_NOT_CONFIGURED, E.BACKEND_NOT_READY, E.OUT_OF_SPACE):
                    break
    for t in takes:
        pipeline.append_manifest(s, root, {"time": pipeline.now(), "tool": "generate_variations", "description": desc, **t})
    if not takes and failures:
        first = failures[0]
        raise E.AudioPluginError(first.get("code", E.BACKEND_ERROR), first.get("message", "all takes failed"),
                                 hint=first.get("hint"), failures=failures)
    return {"success": True, "partial": bool(failures), "requested": count, "generated": len(takes),
            "takes": takes, "failures": failures, "engine": (info or {}).get("engine")}


# ----------------------------------------------------------------- analysis
def _guess_kind(path: Path) -> str:
    words = " ".join([path.stem] + [p.name for p in path.parents][:3]).replace("_", " ").replace("-", " ").lower()
    if re.search(r"\b(music|bgm|soundtrack|ost|theme|song)\b", words):
        return "music"
    if re.search(r"\b(stinger|jingle|sting|fanfare)\b", words):
        return "stinger"
    if re.search(r"\b(ambience|ambient|amb|atmos)\b", words):
        return "ambience"
    if re.search(r"\b(ui|click|button|menu)\b", words):
        return "ui"
    return "sfx"


def _load_any(s: Settings, path: Path) -> bytes:
    try:
        return decode_any(path, _ffmpeg(s))
    except OSError as exc:
        raise E.AudioPluginError(E.INVALID_INPUT, f"Cannot read {path.name}: {exc}") from None


def _resolve_audio_file(s: Settings, args: dict[str, Any]) -> tuple[Path, Path | None]:
    raw = _opt_str(args, "path", 2000)
    if not raw:
        raise E.AudioPluginError(E.INVALID_INPUT, "path is required")
    root = _root(s, args)
    if root is not None:
        p = safety.resolve_inside(root, raw, must_exist=True)
    else:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise E.AudioPluginError(E.INVALID_INPUT, "Give an absolute path, or project_path plus a project-relative path")
        if safety.is_link(p):
            raise E.AudioPluginError(E.PATH_REJECTED, "Refusing to follow a link")
        allowed = s.values["allowed_project_roots"]
        inside_data = s.data_dir.resolve() in p.resolve().parents
        if allowed and not inside_data:
            safety.resolve_project_root(str(p.parent), allowed)
        if not p.is_file():
            raise E.AudioPluginError(E.INVALID_INPUT, f"File not found: {p}")
    if p.stat().st_size > 300 * 1024 * 1024:
        raise E.AudioPluginError(E.INVALID_INPUT, "File is larger than 300 MB")
    return p, root


@_respond
def analyze_audio(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    path, root = _resolve_audio_file(s, args)
    kind = _kind(args, _guess_kind(path), ALL_KINDS)
    bpm = _opt_num(args, "bpm", 40, 240)
    raw = _load_any(s, path)
    a = wav.decode(raw)
    embedded = wav.read_loop(raw) if path.suffix.lower() == ".wav" else None
    loop = _bool(args, "loop", bool(embedded))
    rep = analysis.analyze(a, kind=kind, expected_seconds=_opt_num(args, "expected_seconds", 0.05, 3600), bpm=bpm, loop=loop)
    out = {"success": True, "path": str(path), "format": path.suffix.lower().lstrip("."), **rep,
           "embedded_loop": {"start": embedded[0], "end": embedded[1]} if embedded else None}
    desc = _opt_str(args, "description", MAX_PROMPT_CHARS)
    if desc:
        tmp_dir = s.renders_dir
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / f"analyze-{uuid.uuid4().hex}.wav"
        tmp.write_bytes(raw)
        try:
            res = semantic.score([tmp], desc, kind, python=semantic.find_python(s.values["clap_python"]),
                                 model=s.values["clap_model"])
        finally:
            tmp.unlink(missing_ok=True)
        if res.get("available"):
            sc = res["scores"][0]
            out["semantic"] = {**sc, "points": round(semantic.semantic_points(sc["cosine"]), 1), "model": res["model"]}
        else:
            out["semantic"] = {"available": False, "reason": res.get("reason")}
    fixes = [i["code"] for i in rep["issues"] if i["fixable_by_mastering"]]
    out["recommendations"] = (["Run process_audio with the matching kind to fix: " + ", ".join(fixes)] if fixes else []) + \
        (["Regenerate: issues that mastering cannot fix: " + ", ".join(i["code"] for i in rep["issues"] if not i["fixable_by_mastering"] and i["severity"] != "info")]
         if any(not i["fixable_by_mastering"] and i["severity"] != "info" for i in rep["issues"]) else [])
    for i in out["issues"]:
        i.pop("penalty", None)
    return out


@_respond
def process_audio(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    path, root = _resolve_audio_file(s, args)
    kind = _kind(args, _guess_kind(path), ALL_KINDS)
    bpm = _opt_num(args, "bpm", 40, 240)
    loop = _bool(args, "loop", False)
    beats = int(_num(args, "beats_per_bar", 4, 2, 7))
    loop_seconds = _opt_num(args, "loop_seconds", 0.2, 600)
    raw = _load_any(s, path)
    a = wav.decode(raw)
    before = analysis.analyze(a, kind=kind, bpm=bpm)
    mo = _master_opts(kind, args, loop=loop, bpm=bpm, beats_per_bar=beats, loop_seconds=loop_seconds)
    if args.get("crossfade_seconds") is not None:
        mo.crossfade_seconds = _num(args, "crossfade_seconds", 1.0, 0.01, 8.0)
    if loop and loop_seconds is not None:
        _check_loop_fits(loop_seconds, mo.crossfade_seconds if mo.crossfade_seconds is not None else 0.05)
    if not mo.enabled:
        raise E.AudioPluginError(E.INVALID_INPUT, "Nothing to do: processing is disabled")
    try:
        out_audio, steps, loop_info = mastering.master(a, mo)
    except ValueError as exc:
        raise E.AudioPluginError(E.INVALID_INPUT, f"Cannot process: {exc}") from None
    after = analysis.analyze(out_audio, kind=kind, bpm=bpm, loop=loop, loop_info=loop_info)
    data = wav.encode(out_audio, loop=loop)
    fmt = _format(s, args)
    suffix = _opt_str(args, "output_suffix", 40) or ("_loop" if loop else "_mastered")
    stem = safety.safe_stem(path.stem + suffix) if not re.fullmatch(r"[A-Za-z0-9_\-]+", path.stem + suffix) else path.stem + suffix
    ext = ".ogg" if fmt == "ogg" and _ffmpeg(s) else ".wav"
    format_note = "ffmpeg not found; saved WAV instead of OGG" if fmt == "ogg" and ext == ".wav" else None
    target = safety.unique_path(path.parent, stem, ext)
    if root is not None:
        target = safety.resolve_inside(root, target.relative_to(root).as_posix())
    if ext == ".ogg":
        from .codecs_io import encode_ogg

        encode_ogg(data, target, _ffmpeg(s), loop_frames=out_audio.frames if loop else None)
    else:
        safety.publish_exclusive(data, target)
    res = {"success": True, "source": str(path), "path": str(target), "kind": kind, "steps": steps, "loop": loop_info or None,
           "before": {"score": before["score"], "metrics": before["metrics"], "issues": [i["code"] for i in before["issues"]]},
           "after": {"score": after["score"], "metrics": after["metrics"], "issues": [i["code"] for i in after["issues"]]},
           "note": "The original file was not modified."}
    if format_note:
        res["format_note"] = format_note
    if root is not None:
        adapter, _ = detect_engine(root)
        _with_project_ref(res, adapter, root)
    return res


@_respond
def inspect_game_audio(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    root = safety.resolve_project_root(_opt_str(args, "project_path", 2000) or "", s.values["allowed_project_roots"])
    adapter, info = detect_engine(root)
    inv = adapter.inventory()
    cap = int(_num(args, "max_assets", 200, 1, 2000))
    total = len(inv["audio_assets"])
    inv["audio_assets"] = inv["audio_assets"][:cap]
    suggested_dir, style = _conventional_dir(adapter)
    if adapter.name == "godot":
        inv["godot_executable"] = find_godot(s.values["godot_executable"])
    return {
        "success": True,
        "project_root": str(root),
        "engine": info,
        "audio_asset_count": total,
        "suggested_audio_dir": suggested_dir,
        "suggested_audio_dir_ref": adapter.engine_ref(suggested_dir) if suggested_dir != "." else adapter.engine_ref(""),
        "naming_style": style,
        "code_integration_supported": adapter.supports_code_integration,
        **inv,
        "read_only": True,
    }


def _match_existing(adapter, name: str) -> str | None:  # noqa: ANN001
    from .game_adapters.scan import AUDIO_EXTS, walk

    want = safety.safe_stem(name).replace("-", "_")
    best = None
    for p in walk(adapter.root, max_files=8000).files:
        if p.suffix.lower() not in AUDIO_EXTS:
            continue
        stem = re.sub(r"(?<!^)(?=[A-Z])", "_", p.stem).lower().replace("-", "_")
        stem = re.sub(r"_+", "_", stem)
        if stem == want or re.fullmatch(re.escape(want) + r"(_v?\d{1,3})?", stem):
            rel = p.relative_to(adapter.root).as_posix()
            if best is None or len(rel) < len(best):
                best = rel
    return best


@_respond
def prepare_game_audio(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    root = _root(s, args, required=True)
    feature = _opt_str(args, "feature", 300) or ""
    sounds = args.get("sounds")
    if not isinstance(sounds, list) or not sounds:
        raise E.AudioPluginError(E.INVALID_INPUT, "sounds must be a non-empty list")
    if len(sounds) > MAX_SOUNDS_PER_FEATURE:
        raise E.AudioPluginError(E.INVALID_INPUT, f"At most {MAX_SOUNDS_PER_FEATURE} sounds per call")
    style = _opt_str(args, "style", 200)
    dry = bool(args.get("dry_run", False))
    adapter, info = detect_engine(root)
    custom_dir = _opt_str(args, "audio_dir")

    plan = []
    seen: set[str] = set()
    for i, item in enumerate(sounds):
        if not isinstance(item, dict):
            raise E.AudioPluginError(E.INVALID_INPUT, f"sounds[{i}] must be an object")
        name = _opt_str(item, "name", 120)
        desc = _opt_str(item, "description", MAX_PROMPT_CHARS)
        if not name or not desc:
            raise E.AudioPluginError(E.INVALID_INPUT, f"sounds[{i}] needs name and description")
        kind = _kind(item, "sfx", ALL_KINDS)
        if custom_dir:
            sub = custom_dir if kind in ("sfx", "ui") else (_opt_str(args, "music_dir") or custom_dir)
            _, naming = _conventional_dir(adapter, quick=True, kind=kind)
        else:
            sub, naming = _conventional_dir(adapter, kind=kind)
        stem = _final_stem(name, naming)
        music = kind in ("music", "stinger")
        lim = s.max_music_duration if music else s.max_duration
        default = float(s.values["default_music_seconds"]) if kind == "music" else (4.0 if kind == "stinger" else (20.0 if kind == "ambience" else s.default_duration))
        secs = _num(item, "duration_seconds", min(default, lim), 1.0 if music else 0.1, lim)
        takes = int(_num(item, "variations", 1, 1, s.max_variations)) if not music and kind != "ambience" else 1
        produced = [stem] if takes == 1 else [f"{stem}_v{t + 1:02d}" for t in range(takes)]
        for st in produced:
            if st.lower() in seen:
                raise E.AudioPluginError(E.INVALID_INPUT, f"Sound name '{name}' collides with another planned file ({st})")
            seen.add(st.lower())
        existing = _match_existing(adapter, name) if item.get("reuse_existing", True) is not False else None
        entry = {"name": name, "kind": kind, "stem": stem, "description": desc, "duration_seconds": secs, "variations": takes,
                 "audio_dir": sub, "action": "reuse" if existing else "generate", "existing_asset": existing,
                 "existing_ref": adapter.engine_ref(existing) if existing else None, "item": item}
        if music:
            entry["prompt"] = build_music_prompt(desc, kind=kind, genre=_opt_str(item, "genre", 120) or style,
                                                 mood=_opt_str(item, "mood", 120), bpm=_opt_num(item, "bpm", 40, 240),
                                                 key=_key(item), loop=_bool(item, "loop", kind == "music"))
        else:
            entry["prompt"] = build_sfx_prompt(desc, style, "", kind)
        plan.append(entry)
    base = {"success": True, "project_root": str(root), "engine": info, "feature": feature}
    public_plan = [{k: v for k, v in p.items() if k != "item"} for p in plan]
    if dry:
        return {**base, "dry_run": True, "plan": public_plan}

    failures: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    fatal = (E.BACKEND_NOT_CONFIGURED, E.BACKEND_NOT_READY, E.OUT_OF_SPACE)
    for p in plan:
        if p["action"] == "reuse":
            assets.append({"name": p["name"], "kind": p["kind"], "status": "reused", "path": p["existing_asset"], "engine_ref": p["existing_ref"]})
            continue
        item = dict(p["item"])
        item.setdefault("candidates", args.get("candidates"))
        for k in ("backend", "post_process", "output_format", "min_score", "max_attempts"):
            if k not in item and args.get(k) is not None:
                item[k] = args[k]
        item["project_path"] = str(root)
        item["subdir"] = p["audio_dir"]
        item["duration_seconds"] = p["duration_seconds"]
        if style and "style" not in item and p["kind"] not in ("music", "stinger"):
            item["style"] = style
        if p["kind"] in ("music", "stinger"):
            item.setdefault("genre", style)
        stems = [p["stem"]] if p["variations"] == 1 else [f"{p['stem']}_v{t + 1:02d}" for t in range(p["variations"])]
        for t, stem in enumerate(stems):
            item["filename"] = stem
            if p["variations"] > 1:
                item["description"] = p["description"] + (", " + _VARIATION_HINTS[t % len(_VARIATION_HINTS)] if t else "")
            fn = generate_music if p["kind"] in ("music", "stinger") else generate_sound
            r = json.loads(fn(rt, {k: v for k, v in item.items() if v is not None and k not in ("name", "variations", "reuse_existing")}))
            if not r.get("success"):
                failures.append({"sound": p["name"], "take": t + 1, **r["error"]})
                if r["error"]["code"] in fatal:
                    break
                continue
            assets.append({"name": p["name"], "kind": p["kind"], "status": "generated", "path": r["project_path"],
                           "engine_ref": r["engine_ref"], "backend": r["backend"], "is_ai_model": r["is_ai_model"],
                           "duration_seconds": r["audio"]["duration_seconds"], "quality_score": r["quality"]["score"],
                           "quality_verdict": r["quality"]["verdict"], "loop": bool(r.get("loop"))})
        if failures and failures[-1]["code"] in fatal:
            break
    entry = {"time": pipeline.now(), "tool": "prepare_game_audio", "feature": feature, "assets": assets}
    manifest = pipeline.append_manifest(s, root, entry)
    if not assets and failures:
        f0 = failures[0]
        raise E.AudioPluginError(f0.get("code", E.BACKEND_ERROR), f0.get("message", "generation failed"), hint=f0.get("hint"), failures=failures)
    return {**base, "assets": assets, "failures": failures, "partial": bool(failures), "manifest": str(manifest),
            "next_step": "Call integrate_game_audio (action='preview' then 'apply'); use mode='music' or 'ambience' for looping beds."}


# ------------------------------------------------------------- integration
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _record_path(s: Settings, integration_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{8}", integration_id or ""):
        raise E.AudioPluginError(E.INVALID_INPUT, "integration_id is malformed")
    return s.backups_dir / integration_id / "record.json"


@_respond
def integrate_game_audio(rt: Runtime, args: dict[str, Any]) -> dict[str, Any]:
    s = rt.settings()
    root = safety.resolve_project_root(_opt_str(args, "project_path", 2000) or "", s.values["allowed_project_roots"])
    action = args.get("action") or "preview"
    if action not in ("preview", "apply", "revert"):
        raise E.AudioPluginError(E.INVALID_INPUT, "action must be preview, apply or revert")
    if action == "revert":
        return _revert(s, root, _opt_str(args, "integration_id", 64) or "", bool(args.get("force", False)))

    adapter, info = detect_engine(root)
    sound_raw = _opt_str(args, "sound", 1000)
    if not sound_raw:
        raise E.AudioPluginError(E.INVALID_INPUT, "sound is required")
    sound_path = safety.resolve_inside(root, sound_raw, must_exist=True)
    sound_rel = sound_path.relative_to(root).as_posix()
    if sound_path.suffix.lower() == ".wav":
        wav.validate(sound_path.read_bytes(), max_seconds=3600)
    function = _opt_str(args, "function", 120)

    mode = args.get("mode") or "auto"
    if mode not in ("auto", "global", "2d", "3d", "music", "ambience"):
        raise E.AudioPluginError(E.INVALID_INPUT, "mode must be auto, global, 2d, 3d, music or ambience")
    if not isinstance(adapter, GodotAdapter):
        manual = adapter.manual_integration(sound_rel, function=function)
        return {"success": True, "partial": True, "engine": info, "sound": sound_rel, **manual,
                "validation": {"level": "none", "playback": "unverified"}}
    if str(info.get("version", "")).startswith("3"):
        raise E.AudioPluginError(E.UNSUPPORTED_ENGINE, "Automatic script edits target Godot 4; this is a Godot 3 project",
                                 hint="Use the asset path manually or upgrade the project.")
    script_raw = _opt_str(args, "script", 1000)
    if not script_raw or not function:
        raise E.AudioPluginError(E.INVALID_INPUT, "script and function are required for Godot integration")
    if not re.fullmatch(r"[A-Za-z_]\w*", function):
        raise E.AudioPluginError(E.INVALID_INPUT, "function must be a GDScript identifier")
    script_path = safety.resolve_inside(root, script_raw, must_exist=True)
    if script_path.suffix != ".gd":
        raise E.AudioPluginError(E.INVALID_INPUT, "script must be a .gd file (C# scripts are not edited automatically)")
    script_rel = script_path.relative_to(root).as_posix()
    before_bytes = script_path.read_bytes()
    try:
        before = before_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise E.AudioPluginError(E.INTEGRATION_FAILED, f"{script_rel} is not UTF-8 text") from None
    bom = "\ufeff" if before.startswith("\ufeff") else ""
    plan = adapter.plan_script_edit(
        before[len(bom):],
        function=function,
        asset_ref=adapter.engine_ref(sound_rel),
        mode=mode,
        fade_in=_num(args, "fade_in_seconds", 1.0, 0.0, 10.0),
        volume_db=_num(args, "volume_db", 0.0, -60.0, 24.0),
        pitch_variation=_num(args, "pitch_variation", 0.05, 0.0, 0.3),
        anchor=_opt_str(args, "anchor", 200),
    )
    after = bom + plan["text"]
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{script_rel}", f"b/{script_rel}"))
    base = {"success": True, "engine": info, "sound": sound_rel, "sound_ref": adapter.engine_ref(sound_rel),
            "script": script_rel, "function": function, "mode": plan["mode"], "changes": plan["changes"], "diff": diff}
    if plan["noop"] or before == after:
        return {**base, "status": "already_integrated", "applied": False, "diff": ""}
    if action == "preview":
        return {**base, "status": "preview", "applied": False, "next_step": "Call again with action='apply' to write this change."}

    # apply: back up first, then write atomically (fails if the file changed meanwhile)
    integration_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    bdir = s.backups_dir / integration_id
    (bdir / "files").mkdir(parents=True, exist_ok=False)
    backup_file = bdir / "files" / (script_rel.replace("/", "__"))
    backup_file.write_bytes(before_bytes)
    (bdir / "change.patch").write_text(diff, encoding="utf-8")
    safety.atomic_replace_text(script_path, before, after)
    after_bytes = script_path.read_bytes()
    record = {
        "integration_id": integration_id, "time": pipeline.now(), "project_root": str(root), "engine": "godot",
        "sound": sound_rel, "function": function,
        "files": [{"path": script_rel, "backup": str(backup_file), "sha256_before": _sha(before_bytes), "sha256_after": _sha(after_bytes)}],
        "reverted": False,
    }
    (bdir / "record.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    checks = adapter.static_checks(after, sound_rel, plan["helper"])
    level = "static_checked" if all(c["ok"] for c in checks) else "static_failed"
    engine_res = None
    godot = find_godot(s.values["godot_executable"])
    if args.get("run_engine_checks", True) is not False:
        if godot:
            engine_res = adapter.engine_checks(godot, script_rel)
            sc = engine_res.get("script_check") or {}
            if sc.get("ok") and level == "static_checked":
                level = "engine_parsed"
            elif sc and not sc.get("ok"):
                level = "engine_parse_failed"
        else:
            engine_res = {"skipped": "Godot executable not found (set godot_executable to enable headless import + parse checks)"}
    return {
        **base, "status": "applied", "applied": True, "integration_id": integration_id,
        "changed_files": [script_rel], "backup_dir": str(bdir),
        "validation": {"level": level, "static_checks": checks, "engine": engine_res,
                       "playback": "unverified — run the game and trigger the event to hear it"},
        "revert": {"action": "revert", "integration_id": integration_id},
    }


def _revert(s: Settings, root: Path, integration_id: str, force: bool) -> dict[str, Any]:
    rec_path = _record_path(s, integration_id)
    if not rec_path.is_file():
        raise E.AudioPluginError(E.INVALID_INPUT, f"No integration record {integration_id}")
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    if Path(rec["project_root"]) != root:
        raise E.AudioPluginError(E.PATH_REJECTED, "That integration belongs to a different project")
    if rec.get("reverted"):
        return {"success": True, "status": "already_reverted", "integration_id": integration_id}
    restored = []
    for f in rec["files"]:
        target = safety.resolve_inside(root, f["path"], must_exist=True)
        current = target.read_bytes()
        if _sha(current) != f["sha256_after"] and not force:
            raise E.AudioPluginError(E.CONFLICT, f"{f['path']} was edited after the integration; nothing reverted",
                                     hint="Review manually, or pass force=true to restore the backup anyway.")
        original = Path(f["backup"]).read_bytes()
        # bytes-level atomic restore
        tmp = target.with_name(f".hlga-revert-{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(original)
        shutil.copymode(target, tmp)
        tmp.replace(target)
        restored.append(f["path"])
    rec["reverted"] = True
    rec["reverted_at"] = pipeline.now()
    rec_path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return {"success": True, "status": "reverted", "integration_id": integration_id, "restored_files": restored,
            "note": "Generated audio files were left in place."}


HANDLERS = {
    "audio_status": audio_status,
    "generate_sound": generate_sound,
    "generate_music": generate_music,
    "generate_variations": generate_variations,
    "analyze_audio": analyze_audio,
    "process_audio": process_audio,
    "inspect_game_audio": inspect_game_audio,
    "prepare_game_audio": prepare_game_audio,
    "integrate_game_audio": integrate_game_audio,
}
