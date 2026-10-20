"""Optional semantic check: does the audio match the text? (CLAP, local).

The model runs in a separate Python environment chosen by the user (setting
``clap_python``) so torch/transformers never enter Hermes's environment.
When it is not configured, scoring is skipped and reported as such.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from ..safety import child_env

WORKER = Path(__file__).with_name("clap_worker.py")
DEFAULT_MODEL = "laion/larger_clap_general"
CONTRASTS = {
    "sfx": ["silence", "static noise", "a person speaking", "background music"],
    "ui": ["silence", "static noise", "a person speaking", "background music"],
    "music": ["silence", "static noise", "a person speaking", "a single short sound effect"],
    "stinger": ["silence", "static noise", "a person speaking", "a single short sound effect"],
    "ambience": ["silence", "a person speaking", "music with melody"],
}


def find_python(configured: str = "") -> str | None:
    for raw in (configured, os.environ.get("HLGA_CLAP_PYTHON", "")):
        if raw and Path(raw).expanduser().is_file():
            return str(Path(raw).expanduser())
    return None


def _env(offline: bool) -> dict[str, str]:
    env = child_env()
    for k in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(k, None)
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    env["PYTHONUTF8"] = "1"
    return env


def score(files: list[Path], prompt: str, kind: str, *, python: str | None, model: str = DEFAULT_MODEL,
          timeout: int = 600) -> dict[str, Any]:
    """Return {"available": bool, "scores": [{"cosine", "prompt_probability"}...], ...}."""
    if not python:
        return {"available": False, "reason": "CLAP scorer not configured (set clap_python; see docs/QUALITY.md)"}
    texts = [prompt] + CONTRASTS.get(kind, CONTRASTS["sfx"])
    req = {"model": model, "texts": texts, "files": [str(f) for f in files]}
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, JSON over stdin
            [python, "-I", str(WORKER)], input=json.dumps(req), capture_output=True, text=True,
            timeout=timeout, env=_env(offline=True), check=False,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": f"CLAP scorer timed out after {timeout}s"}
    except OSError as exc:
        return {"available": False, "reason": f"Could not run CLAP python: {exc}"}
    try:
        out = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"available": False, "reason": "CLAP scorer returned no result", "stderr_tail": proc.stderr[-800:]}
    if not out.get("ok"):
        return {"available": False, "reason": out.get("error", "CLAP scorer failed")}
    scores = [{"cosine": round(row[0], 4), "prompt_probability": p} for row, p in zip(out["cosine"], out["prompt_probability"])]
    return {"available": True, "model": model, "scores": scores}


def warmup(python: str, model: str = DEFAULT_MODEL, timeout: int = 1800) -> dict[str, Any]:
    proc = subprocess.run([python, "-I", str(WORKER), "--warmup", model], capture_output=True, text=True,  # noqa: S603
                          timeout=timeout, env=_env(offline=False), check=False)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": proc.stderr[-800:]}


def semantic_points(cosine: float) -> float:
    """Map CLAP cosine (~0.0 unrelated .. ~0.5 strong match) to 0-100."""
    return max(0.0, min(100.0, (cosine - 0.0) / 0.45 * 100))
