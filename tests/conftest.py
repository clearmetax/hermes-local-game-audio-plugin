from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import _load  # noqa: E402

PLUGIN = _load.load()
from hlga_under_test import tools  # noqa: E402
from hlga_under_test.audio_backend import AgentAudioBackend  # noqa: E402

DEMO = _load.PLUGIN_DIR / "examples" / "godot-demo"
FAKE_SERVER = Path(__file__).parent / "fake_mcp_server.py"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HLGA_DATA_DIR", str(tmp_path / "plugin-data"))
    for key in ("HLGA_BACKEND", "HLGA_MUSIC_BACKEND", "HLGA_AGENT_AUDIO_CHECKOUT", "AGENT_AUDIO_CHECKOUT", "HLGA_ALLOWED_PROJECT_ROOTS",
                "HLGA_COMFYUI_URL", "HLGA_COMFYUI_WORKFLOW_SFX", "HLGA_COMFYUI_WORKFLOW_MUSIC", "HLGA_OUTPUT_FORMAT"):
        monkeypatch.delenv(key, raising=False)
    # Ensure auto-discovery never finds a real checkout on the dev machine.
    monkeypatch.setattr("hlga_under_test.audio_backend.agent_audio._CANDIDATE_DIRS", ())
    monkeypatch.setattr(
        "hlga_under_test.audio_backend.agent_audio.find_checkout",
        lambda configured="": None,
    )
    # CLAP is opt-in for tests (set HLGA_TEST_CLAP_PYTHON to run the real scorer).
    monkeypatch.setenv("HLGA_CLAP_PYTHON", "")
    monkeypatch.setenv("HLGA_DEFAULT_CANDIDATES", "1")
    yield


@pytest.fixture
def rt():
    return tools.Runtime()


@pytest.fixture
def call(rt):
    def _call(name: str, args: dict | None = None) -> dict:
        return json.loads(tools.HANDLERS[name](rt, args or {}))

    return _call


@pytest.fixture
def demo(tmp_path) -> Path:
    dst = tmp_path / "godot-demo"
    shutil.copytree(DEMO, dst, ignore=shutil.ignore_patterns(".godot", "audio", "*.import"))
    return dst


@pytest.fixture
def fake_backend(monkeypatch):
    """Route agent_audio generations to the stdlib fake MCP server."""

    def install(mode: str = "ok", timeout: float = 60):
        monkeypatch.setenv("FAKE_MCP_MODE", mode)

        def make(settings, *, override=None, call_mcp=None, kind="sfx"):
            if override == "procedural":
                from hlga_under_test.audio_backend import ProceduralBackend

                return ProceduralBackend()
            return AgentAudioBackend(argv_override=[sys.executable, "-I", str(FAKE_SERVER)], timeout=timeout)

        from hlga_under_test import pipeline

        monkeypatch.setattr(pipeline, "make_backend", make)

    return install
