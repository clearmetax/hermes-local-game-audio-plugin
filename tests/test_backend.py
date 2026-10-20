import os
import sys
from pathlib import Path

import pytest
from conftest import FAKE_SERVER
from hlga_under_test import errors as E
from hlga_under_test.audio_backend import AgentAudioBackend, GenerationRequest, ProceduralBackend
from hlga_under_test.audio_backend.agent_audio import is_checkout
from hlga_under_test.audio_backend.mcp_stdio import StdioMcpClient


def backend(timeout=30):
    return AgentAudioBackend(argv_override=[sys.executable, "-I", str(FAKE_SERVER)], timeout=timeout)


def test_mcp_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MCP_MODE", "ok")
    with backend().session() as s:
        out = s.generate(GenerationRequest("boom", 0.3, tmp_path / "a.wav"))
    assert out.is_file() and out.stat().st_size > 1000


def test_status_deep_ready_and_not_ready(monkeypatch):
    monkeypatch.setenv("FAKE_MCP_MODE", "ok")
    assert backend().status(deep=True).ready is True
    monkeypatch.setenv("FAKE_MCP_MODE", "notready")
    st = backend().status(deep=True)
    assert st.ready is False and st.problems


@pytest.mark.parametrize(
    "mode,code",
    [("error", E.BACKEND_ERROR), ("notready", E.BACKEND_NOT_READY), ("crash", E.BACKEND_ERROR), ("wrongpath", E.INVALID_OUTPUT)],
)
def test_backend_failures_are_structured(monkeypatch, tmp_path, mode, code):
    monkeypatch.setenv("FAKE_MCP_MODE", mode)
    with pytest.raises(E.AudioPluginError) as ei:
        with backend().session() as s:
            s.generate(GenerationRequest("boom", 0.3, tmp_path / "a.wav"))
    assert ei.value.code == code


def test_timeout_kills_server(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_MCP_MODE", "slow")
    b = backend(timeout=2)
    with pytest.raises(E.AudioPluginError) as ei:
        with b.session() as s:
            proc_holder = s.client
            s.generate(GenerationRequest("boom", 0.3, tmp_path / "a.wav"))
    assert ei.value.code == E.GENERATION_TIMEOUT
    assert proc_holder.proc is None  # closed and reaped


def test_incompatible_server(monkeypatch):
    monkeypatch.setenv("FAKE_MCP_MODE", "notools")
    with pytest.raises(E.AudioPluginError, match="does not expose"):
        with backend().session():
            pass


def test_missing_checkout_is_not_configured():
    b = AgentAudioBackend(checkout="/definitely/not/here")
    st = b.status()
    assert st.configured is False and st.setup_steps
    with pytest.raises(E.AudioPluginError) as ei:
        with b.session():
            pass
    assert ei.value.code == E.BACKEND_NOT_CONFIGURED


def test_checkout_without_venv_is_not_ready(tmp_path, monkeypatch):
    co = tmp_path / "agent-audio"
    (co / "src" / "agent_audio").mkdir(parents=True)
    (co / "src" / "agent_audio" / "mcp_server.py").write_text("")
    (co / "pyproject.toml").write_text('[project]\nname = "agent-audio"\n')
    assert is_checkout(co)
    import hlga_under_test.audio_backend.agent_audio as aa

    monkeypatch.setattr(aa, "find_checkout", lambda configured="": co)
    with pytest.raises(E.AudioPluginError) as ei:
        with AgentAudioBackend(checkout=str(co)).session():
            pass
    assert ei.value.code == E.BACKEND_NOT_READY


def test_hermes_mcp_transport_permission_error():
    def call_mcp(server, tool, args, timeout=30):
        raise PermissionError("not allowed")

    b = AgentAudioBackend(transport="hermes_mcp", call_mcp=call_mcp)
    with pytest.raises(E.AudioPluginError) as ei:
        with b.session() as s:
            s.generate(GenerationRequest("x", 1, Path("/tmp/none.wav")))
    assert ei.value.code == E.BACKEND_NOT_CONFIGURED and "mcp_allowlist" in ei.value.hint


def test_hermes_mcp_transport_success(tmp_path):
    from conftest import FAKE_SERVER  # noqa: F401

    out = tmp_path / "r.wav"

    def call_mcp(server, tool, args, timeout=30):
        assert server == "agent-audio" and timeout <= 600
        if tool == "audio_status":
            return {"ok": True, "result": "{}", "structuredContent": {"runtime_ready": True}}
        Path(args["output_path"]).write_bytes(b"RIFF")
        return {"ok": True, "result": args["output_path"]}

    b = AgentAudioBackend(transport="hermes_mcp", call_mcp=call_mcp)
    assert b.status(deep=True).ready
    with b.session() as s:
        assert s.generate(GenerationRequest("x", 1, out)) == out


def test_isolated_env_strips_python_vars(monkeypatch):
    from hlga_under_test.audio_backend.mcp_stdio import isolated_env

    monkeypatch.setenv("PYTHONPATH", "/evil")
    env = isolated_env({"AGENT_AUDIO_HOME": "/x"})
    assert "PYTHONPATH" not in env and env["AGENT_AUDIO_HOME"] == "/x" and env["PYTHONNOUSERSITE"] == "1"


def test_client_rejects_shell_strings():
    with pytest.raises(ValueError):
        StdioMcpClient([])  # type: ignore[arg-type]


def test_procedural_backend_deterministic(tmp_path):
    b = ProceduralBackend()
    with b.session() as s:
        a = s.generate(GenerationRequest("laser zap", 0.3, tmp_path / "a.wav", seed=7)).read_bytes()
        c = s.generate(GenerationRequest("laser zap", 0.3, tmp_path / "c.wav", seed=7)).read_bytes()
    assert a == c


@pytest.mark.skipif(not os.environ.get("HLGA_REAL_AGENT_AUDIO_CHECKOUT"), reason="set HLGA_REAL_AGENT_AUDIO_CHECKOUT to test a real Agent Audio install")
def test_real_agent_audio_status():
    """Real stdio MCP round trip with the actual agent_audio.mcp_server (no model needed)."""
    from hlga_under_test.audio_backend.mcp_stdio import python_in_venv

    co = Path(os.environ["HLGA_REAL_AGENT_AUDIO_CHECKOUT"])
    argv = [str(python_in_venv(co / ".venv")), "-I", "-m", "agent_audio.mcp_server"]
    st = AgentAudioBackend(argv_override=argv).status(deep=True)
    assert "upstream_status" in st.details, st.problems
    assert "runtime_ready" in st.details["upstream_status"]


@pytest.mark.skipif(os.environ.get("HLGA_REAL_GENERATION") != "1", reason="set HLGA_REAL_GENERATION=1 (needs installed models) to run real inference")
def test_real_agent_audio_generation(tmp_path):
    from hlga_under_test import wav
    from hlga_under_test.audio_backend.mcp_stdio import python_in_venv

    co = Path(os.environ["HLGA_REAL_AGENT_AUDIO_CHECKOUT"])
    argv = [str(python_in_venv(co / ".venv")), "-I", "-m", "agent_audio.mcp_server"]
    import time

    t0 = time.monotonic()
    with AgentAudioBackend(argv_override=argv, timeout=600).session() as s:
        out = s.generate(GenerationRequest("Heavy metallic impact, sharp transient, isolated sound effect, no voice, no music", 2.0, tmp_path / "real.wav"))
    info = wav.validate(out.read_bytes())
    print(f"real generation: {time.monotonic() - t0:.1f}s -> {info.to_dict()}")
    assert info.duration_seconds > 1.0
