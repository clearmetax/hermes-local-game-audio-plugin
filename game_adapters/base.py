"""Engine adapter interface + the shared (engine-agnostic) inventory logic."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .. import wav
from .scan import (
    AUDIO_EXTS,
    CODE_EXTS,
    PLAY_PATTERNS,
    classify_event,
    functions_in,
    naming_style,
    read_text,
    walk,
)

_AUDIO_LITERAL = re.compile(r"""["']([^"'\n]{1,300}\.(?:wav|ogg|mp3|flac|opus))["']""", re.I)


class EngineAdapter:
    name = "generic"
    display_name = "Unknown / generic project"
    default_audio_dir = "assets/audio/sfx"
    reference_exts: set[str] = CODE_EXTS | {".json", ".html", ".tscn", ".tres", ".unity", ".prefab", ".asset", ".yaml", ".toml"}
    supports_code_integration = False

    def __init__(self, root: Path):
        self.root = root

    # -- detection -----------------------------------------------------------
    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:  # pragma: no cover - overridden
        return None

    # -- engine paths ----------------------------------------------------------
    def engine_ref(self, rel: str) -> str:
        return rel

    def rel_from_ref(self, ref: str) -> str:
        return ref

    # -- inventory -------------------------------------------------------------
    def inventory(self) -> dict[str, Any]:
        w = walk(self.root)
        audio: list[dict[str, Any]] = []
        texts: dict[str, str] = {}
        for p in w.files:
            ext = p.suffix.lower()
            rel = p.relative_to(self.root).as_posix()
            if ext in AUDIO_EXTS:
                item: dict[str, Any] = {"path": rel, "ref": self.engine_ref(rel), "size_bytes": p.stat().st_size}
                if ext == ".wav":
                    try:
                        info = wav.inspect_file(p, measure_peak=False)
                        item.update(duration_seconds=round(info.duration_seconds, 3), sample_rate=info.sample_rate, channels=info.channels)
                    except Exception as exc:  # malformed asset: report, don't crash
                        item["problem"] = getattr(exc, "message", str(exc))
                audio.append(item)
            elif ext in self.reference_exts:
                t = read_text(p)
                if t is not None:
                    texts[rel] = t
        refs = self.find_references(audio, texts)
        for item in audio:
            item["referenced_by"] = refs.get(item["path"], [])[:10]
        missing = self.find_missing(texts, {a["path"] for a in audio})
        gaps = self.find_gaps(texts)
        dirs: dict[str, int] = {}
        for a in audio:
            d = a["path"].rsplit("/", 1)[0] if "/" in a["path"] else "."
            dirs[d] = dirs.get(d, 0) + 1
        stems = [Path(a["path"]).stem for a in audio]
        return {
            "audio_assets": audio,
            "audio_dirs": dict(sorted(dirs.items(), key=lambda kv: -kv[1])),
            "naming_style": naming_style(stems),
            "unreferenced_assets": [a["path"] for a in audio if not a["referenced_by"]],
            "missing_references": missing,
            "candidate_gaps": gaps,
            "files_scanned": len(w.files),
            "scan_truncated": w.truncated,
            "links_skipped": w.skipped_links,
        }

    def find_references(self, audio: list[dict[str, Any]], texts: dict[str, str]) -> dict[str, list[str]]:
        refs: dict[str, list[str]] = {}
        for item in audio:
            name = Path(item["path"]).name
            hits = [rel for rel, t in texts.items() if name in t]
            refs[item["path"]] = hits
        return refs

    def find_missing(self, texts: dict[str, str], existing: set[str]) -> list[dict[str, str]]:
        missing = []
        existing_names = {Path(e).name for e in existing}
        for rel, t in texts.items():
            for m in _AUDIO_LITERAL.finditer(t):
                lit = m.group(1)
                if "{" in lit or "$" in lit or "%" in lit:
                    continue
                candidate = self.rel_from_ref(lit).lstrip("./")
                if candidate in existing or Path(candidate).name in existing_names:
                    continue
                missing.append({"reference": lit, "in_file": rel})
        return missing[:100]

    def find_gaps(self, texts: dict[str, str]) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        for rel, t in texts.items():
            ext = Path(rel).suffix.lower()
            if ext not in CODE_EXTS:
                continue
            for fn in functions_in(t, ext):
                cls = classify_event(fn.name)
                if not cls:
                    continue
                has_audio = bool(PLAY_PATTERNS.search(fn.body))
                if has_audio:
                    continue
                category, desc = cls
                gaps.append({
                    "file": rel,
                    "function": fn.name,
                    "line": fn.line,
                    "event": category,
                    "suggested_sound": f"{category}",
                    "suggested_description": desc,
                    "confidence": "medium" if category in fn.name.lower() else "low",
                    "note": "Heuristic: event-like function with no audio playback found in its body.",
                })
        return gaps[:60]

    # -- integration -----------------------------------------------------------
    def manual_integration(self, asset_rel: str, **_: Any) -> dict[str, Any]:
        return {
            "applied": False,
            "status": "manual_steps_required",
            "steps": [
                f"The asset is at {asset_rel}.",
                "Load it with your engine/framework's audio API and play it from the relevant event handler.",
            ],
        }
