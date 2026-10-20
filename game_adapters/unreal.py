"""Unreal Engine adapter: detection and source-asset placement only.

Unreal stores sounds as binary ``.uasset`` packages created by the editor's
importer, so a raw WAV is staged in ``SourceAudio/SFX`` (outside ``Content``)
and the user or an editor Python script imports it. No Blueprint or C++ edits
are claimed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import EngineAdapter


class UnrealAdapter(EngineAdapter):
    name = "unreal"
    display_name = "Unreal Engine"
    default_audio_dir = "SourceAudio/SFX"
    reference_exts = {".cpp", ".h", ".ini", ".py"}

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        uprojects = sorted(root.glob("*.uproject"))
        if not uprojects:
            return None
        version = "unknown"
        try:
            data = json.loads(uprojects[0].read_text(encoding="utf-8-sig"))
            version = str(data.get("EngineAssociation") or "unknown")
        except (OSError, ValueError):
            pass
        return {"engine": "unreal", "version": version, "marker": uprojects[0].name, "has_content_dir": (root / "Content").is_dir()}

    def manual_integration(self, asset_rel: str, **kw: Any) -> dict[str, Any]:
        stem = Path(asset_rel).stem
        dest = "/Game/Audio/SFX"
        return {
            "applied": False,
            "status": "manual_import_required",
            "steps": [
                f"In the Unreal Editor, import {asset_rel} into {dest} (Content Browser > Import), or run the Python snippet in the editor's Python console.",
                f"Play it from the relevant event, e.g. Blueprint 'Play Sound at Location' with {dest}/{stem}.",
            ],
            "editor_python_snippet": (
                "import unreal\n"
                "task = unreal.AssetImportTask()\n"
                f"task.filename = r'{asset_rel}'  # relative to the project folder; make absolute if needed\n"
                f"task.destination_path = '{dest}'\n"
                "task.automated = True\n"
                "task.save = True\n"
                "unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])"
            ),
            "verification": "Not verifiable without the Unreal Editor.",
        }
