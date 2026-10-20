"""Unity adapter: detection, GUID-aware inventory and asset placement.

Script wiring is returned as a reviewable C# snippet, not applied: a correct
Unity integration usually involves serialized inspector references that can
only be verified inside the Unity Editor. Unity creates the ``.meta`` file
itself when it imports the WAV; this plugin never writes ``.meta`` files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import EngineAdapter
from .scan import read_text

_GUID = re.compile(r"^guid:\s*([0-9a-f]{32})", re.M)


class UnityAdapter(EngineAdapter):
    name = "unity"
    display_name = "Unity"
    default_audio_dir = "Assets/Audio/SFX"
    reference_exts = {".cs", ".unity", ".prefab", ".asset", ".controller", ".playable"}

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        pv = root / "ProjectSettings" / "ProjectVersion.txt"
        if not ((root / "Assets").is_dir() and pv.is_file()):
            return None
        m = re.search(r"m_EditorVersion:\s*(\S+)", read_text(pv) or "")
        return {"engine": "unity", "version": m.group(1) if m else "unknown", "marker": "ProjectSettings/ProjectVersion.txt"}

    def find_references(self, audio, texts):  # noqa: ANN001
        refs: dict[str, list[str]] = {}
        for item in audio:
            meta = self.root / (item["path"] + ".meta")
            guid = None
            if meta.is_file():
                m = _GUID.search(read_text(meta) or "")
                guid = m.group(1) if m else None
            name = Path(item["path"]).stem
            hits = [rel for rel, t in texts.items() if (guid and guid in t) or (rel.endswith(".cs") and f'"{name}"' in t)]
            refs[item["path"]] = hits
        return refs

    def manual_integration(self, asset_rel: str, **kw: Any) -> dict[str, Any]:
        stem = Path(asset_rel).stem
        field = re.sub(r"[^A-Za-z0-9]", "", stem.title())
        field = field[:1].lower() + field[1:] if field else "sfx"
        fn = kw.get("function") or "OnHit"
        return {
            "applied": False,
            "status": "snippet_provided",
            "steps": [
                f"Let Unity import {asset_rel} (focus the Editor; it generates the .meta file).",
                f"Add the field below to the MonoBehaviour that owns {fn}() and assign the clip in the Inspector.",
                f"Call the play line inside {fn}().",
            ],
            "csharp_snippet": (
                f"[SerializeField] private AudioClip {field}Clip;\n"
                f"// inside {fn}():\n"
                f"if ({field}Clip != null) AudioSource.PlayClipAtPoint({field}Clip, transform.position);"
            ),
            "verification": "Not verifiable outside the Unity Editor; test in Play Mode.",
        }
