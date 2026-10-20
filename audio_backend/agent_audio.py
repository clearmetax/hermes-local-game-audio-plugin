"""Adapter for AIEGOBOT/agent-audio (Stable Audio 3 Medium, fully local).

Verified against agent-audio v0.1 source:
* MCP server module: ``agent_audio.mcp_server`` (FastMCP, stdio transport);
* the installer registers it as ``<checkout>/.venv/<bin>/python -I -m agent_audio.mcp_server``
  with ``AGENT_AUDIO_HOME`` in the environment;
* tools: ``audio_status() -> dict`` and
  ``generate_audio(prompt, seconds=10.0, output_path=None, negative_prompt=None) -> str``;
* ``seconds`` must be > 0 and <= 380; inference is stopped after 540 s;
* nonempty negative prompts are rejected; existing outputs are never overwritten;
* generation sets ``HF_HUB_OFFLINE=1`` so it never downloads at inference time.

Two transports are supported:
* ``sidecar``    – this plugin launches the MCP server from the checkout's own
                   virtualenv for the duration of one tool call (no lingering process);
* ``hermes_mcp`` – reuse an ``agent-audio`` entry already configured in Hermes
                   ``mcp_servers`` via ``ctx.call_mcp`` (requires ``mcp_allowlist``).
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import errors as E
from .base import AudioBackend, BackendStatus, GenerationRequest, Session
from .mcp_stdio import McpError, McpTimeout, StdioMcpClient, isolated_env, python_in_venv

UPSTREAM_MAX_SECONDS = 380.0
REPO_URL = "https://github.com/AIEGOBOT/agent-audio"
_CANDIDATE_DIRS = (
    "agent-audio",
    "src/agent-audio",
    "code/agent-audio",
    "dev/agent-audio",
    "projects/agent-audio",
    "repos/agent-audio",
    "Documents/GitHub/agent-audio",
    "Desktop/agent-audio",
    "OneDrive/Desktop/agent-audio",
    "OneDrive/Documents/GitHub/agent-audio",
)


def is_checkout(path: Path) -> bool:
    try:
        pyproject = (path / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r'^name\s*=\s*"agent-audio"', pyproject, re.M)) and (
        path / "src" / "agent_audio" / "mcp_server.py"
    ).is_file()


def find_checkout(configured: str = "") -> Path | None:
    candidates: list[Path] = []
    for raw in (configured, os.environ.get("AGENT_AUDIO_CHECKOUT", "")):
        if raw and raw.strip():
            candidates.append(Path(raw.strip()).expanduser())
    if configured.strip():
        # An explicit setting wins; do not silently pick a different checkout.
        return candidates[0] if is_checkout(candidates[0]) else None
    plugin_parent = Path(__file__).resolve().parents[2]
    candidates.append(plugin_parent / "agent-audio")
    home = Path.home()
    candidates.extend(home / rel for rel in _CANDIDATE_DIRS)
    for c in candidates:
        if is_checkout(c):
            return c.resolve()
    return None


def _setup_steps(checkout: Path | None) -> list[str]:
    where = str(checkout) if checkout else "~/agent-audio"
    steps = []
    if checkout is None:
        steps.append(f"git clone {REPO_URL} {where}   (any folder works)")
        steps.append("Set this plugin's agent_audio_checkout setting to that folder (or AGENT_AUDIO_CHECKOUT).")
    steps += [
        f"cd {where}",
        "uv sync --frozen",
        "uv run --frozen python install/bootstrap.py --doctor",
        "uv run --frozen python install/bootstrap.py --runtime-only   # downloads ~7.4 GB of model files",
        "Review and accept the Stable Audio 3 Medium / Gemma model terms yourself when prompted.",
        "Then run the audio_status tool with deep_check=true.",
    ]
    return steps


def _map_error(text: str) -> E.AudioPluginError:
    low = text.lower()
    if "not ready" in low or "bootstrap" in low:
        return E.AudioPluginError(E.BACKEND_NOT_READY, text, hint="Finish Agent Audio's runtime install (see audio_status setup_steps).")
    if "exceeded" in low and "seconds" in low:
        return E.AudioPluginError(E.GENERATION_TIMEOUT, text, hint="Try a shorter sound; CPU inference is slow.")
    if "no space" in low or "disk full" in low:
        return E.AudioPluginError(E.OUT_OF_SPACE, text)
    if "valid wav" in low:
        return E.AudioPluginError(E.INVALID_OUTPUT, text)
    return E.AudioPluginError(E.BACKEND_ERROR, text)


def _result_path(text: str, structured: Any) -> str:
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"]
    return text.strip().strip('"')


class _SidecarSession(Session):
    def __init__(self, client: StdioMcpClient, timeout: float):
        self.client = client
        self.timeout = timeout

    def call_status(self) -> dict[str, Any]:
        res = self.client.call_tool("audio_status", {}, timeout=60)
        if res["is_error"]:
            raise _map_error(res["text"] or "audio_status failed")
        if isinstance(res["structured"], dict):
            return res["structured"]
        import json

        try:
            return json.loads(res["text"])
        except ValueError:
            return {"raw": res["text"][:2000]}

    def generate(self, request: GenerationRequest) -> Path:
        args = {"prompt": request.prompt, "seconds": float(request.seconds), "output_path": str(request.output_path)}
        try:
            res = self.client.call_tool("generate_audio", args, timeout=self.timeout)
        except McpTimeout as exc:
            raise E.AudioPluginError(
                E.GENERATION_TIMEOUT,
                f"Generation did not finish within {self.timeout:.0f}s",
                hint="Use a shorter duration or raise generation_timeout_seconds.",
            ) from exc
        except McpError as exc:
            raise E.AudioPluginError(E.BACKEND_ERROR, str(exc), server_log_tail=exc.stderr_tail[-1500:]) from exc
        if res["is_error"]:
            raise _map_error(res["text"] or "generate_audio failed")
        produced = Path(_result_path(res["text"], res["structured"]))
        if produced.resolve() != request.output_path.resolve() or not produced.is_file():
            raise E.AudioPluginError(E.INVALID_OUTPUT, f"Backend reported an unexpected output path: {produced}")
        return produced


class _HermesMcpSession(Session):
    def __init__(self, call_mcp: Callable[..., dict], server: str, timeout: float):
        self.call_mcp = call_mcp
        self.server = server
        self.timeout = min(timeout, 600.0)

    def _call(self, tool: str, args: dict[str, Any], timeout: float) -> Any:
        try:
            env = self.call_mcp(self.server, tool, args, timeout=timeout)
        except PermissionError as exc:
            raise E.AudioPluginError(
                E.BACKEND_NOT_CONFIGURED,
                str(exc),
                hint=f"Add '{self.server}' to plugins.entries.hermes-local-game-audio.mcp_allowlist in config.yaml.",
            ) from exc
        except Exception as exc:  # Hermes MCP client errors
            raise E.AudioPluginError(E.BACKEND_ERROR, f"Hermes MCP call failed: {exc}") from exc
        if not env.get("ok"):
            raise _map_error(str(env.get("error") or "MCP call failed"))
        return env.get("structuredContent") if env.get("structuredContent") is not None else env.get("result")

    def call_status(self) -> dict[str, Any]:
        out = self._call("audio_status", {}, 60)
        return out if isinstance(out, dict) else {"raw": str(out)[:2000]}

    def generate(self, request: GenerationRequest) -> Path:
        args = {"prompt": request.prompt, "seconds": float(request.seconds), "output_path": str(request.output_path)}
        out = self._call("generate_audio", args, self.timeout)
        produced = Path(_result_path(out if isinstance(out, str) else "", out))
        if not produced.is_file() or produced.resolve() != request.output_path.resolve():
            raise E.AudioPluginError(E.INVALID_OUTPUT, f"Backend reported an unexpected output path: {produced}")
        return produced


class AgentAudioBackend(AudioBackend):
    name = "agent_audio"
    is_ai_model = True
    # Stable Audio 3 Medium via Agent Audio v0.1: text + duration only. Tempo/key are
    # requested through the prompt and then measured, not guaranteed.
    supports = {"sfx": True, "music": True, "ambience": True, "bpm": False, "key": False, "seed": False,
                "lyrics": False, "negative_prompt": False, "max_seconds": UPSTREAM_MAX_SECONDS}

    def __init__(
        self,
        *,
        checkout: str = "",
        agent_audio_home: str = "",
        transport: str = "sidecar",
        mcp_server: str = "agent-audio",
        call_mcp: Callable[..., dict] | None = None,
        timeout: float = 600,
        argv_override: list[str] | None = None,
    ):
        self.configured_checkout = checkout
        self.agent_audio_home = agent_audio_home
        self.transport = transport
        self.mcp_server = mcp_server or "agent-audio"
        self.call_mcp = call_mcp
        self.timeout = float(timeout)
        self.argv_override = argv_override  # tests / advanced users

    # -- discovery -----------------------------------------------------------
    def _launch(self) -> tuple[list[str], Path | None, dict[str, str]]:
        extra: dict[str, str] = {}
        home = self.agent_audio_home.strip() or os.environ.get("AGENT_AUDIO_HOME", "")
        if home:
            extra["AGENT_AUDIO_HOME"] = str(Path(home).expanduser())
        if self.argv_override:
            return list(self.argv_override), None, isolated_env(extra)
        checkout = find_checkout(self.configured_checkout)
        if checkout is None:
            raise E.AudioPluginError(
                E.BACKEND_NOT_CONFIGURED,
                "Agent Audio checkout not found",
                hint="Install Agent Audio and set agent_audio_checkout.",
                setup_steps=_setup_steps(None),
            )
        python = python_in_venv(checkout / ".venv")
        if not python.is_file():
            raise E.AudioPluginError(
                E.BACKEND_NOT_READY,
                f"Agent Audio's virtualenv is missing: {python}",
                hint="Run `uv sync --frozen` in the checkout.",
                setup_steps=_setup_steps(checkout),
            )
        # -I: isolated mode, same as Agent Audio's own client registration.
        return [str(python), "-I", "-m", "agent_audio.mcp_server"], checkout, isolated_env(extra)

    def status(self, *, deep: bool = False) -> BackendStatus:
        st = BackendStatus(self.name, configured=False, ready=False, is_ai_model=True)
        st.details["transport"] = self.transport
        st.details["model"] = "Stable Audio 3 Medium (via Agent Audio)"
        st.details["upstream_max_seconds"] = UPSTREAM_MAX_SECONDS
        if self.transport == "hermes_mcp":
            st.details["mcp_server"] = self.mcp_server
            if self.call_mcp is None:
                st.problems.append("Hermes MCP client is not available in this context")
                return st
            st.configured = True
            if not deep:
                st.problems.append("Readiness not checked; run audio_status with deep_check=true")
                return st
        else:
            try:
                argv, checkout, _ = self._launch()
                st.configured = True
                st.details["checkout"] = str(checkout) if checkout else None
                st.details["server_command"] = argv
            except E.AudioPluginError as exc:
                st.problems.append(exc.message)
                st.setup_steps = list(exc.details.get("setup_steps", []))
                return st
            if not deep:
                st.problems.append("Runtime readiness not checked; run audio_status with deep_check=true")
                return st
        try:
            with self.session() as sess:
                upstream = sess.call_status()  # type: ignore[attr-defined]
        except E.AudioPluginError as exc:
            st.problems.append(f"{exc.code}: {exc.message}")
            if exc.details.get("server_log_tail"):
                st.details["server_log_tail"] = exc.details["server_log_tail"]
            return st
        st.details["upstream_status"] = upstream
        st.ready = bool(upstream.get("runtime_ready"))
        if not st.ready:
            st.problems.append("Agent Audio reports runtime_ready=false (models or runtime missing)")
            st.setup_steps = _setup_steps(find_checkout(self.configured_checkout))
        st.details["note"] = "runtime_ready checks files and model headers, not a successful inference"
        return st

    @contextmanager
    def session(self) -> Iterator[Session]:
        if self.transport == "hermes_mcp":
            if self.call_mcp is None:
                raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, "Hermes MCP client unavailable outside Hermes")
            yield _HermesMcpSession(self.call_mcp, self.mcp_server, self.timeout)
            return
        argv, checkout, env = self._launch()
        client = StdioMcpClient(argv, cwd=checkout, env=env)
        try:
            client.start()
            names = {t.get("name") for t in client.list_tools()}
            missing = {"audio_status", "generate_audio"} - names
            if missing:
                raise E.AudioPluginError(
                    E.BACKEND_ERROR,
                    f"Agent Audio MCP server does not expose {sorted(missing)}; incompatible version?",
                )
        except McpError as exc:
            client.close()
            raise E.AudioPluginError(
                E.BACKEND_ERROR,
                f"Could not start Agent Audio MCP server: {exc}",
                hint="Run `uv sync --frozen` in the checkout and check its Python 3.11+ environment.",
                server_log_tail=exc.stderr_tail[-1500:],
            ) from exc
        except BaseException:
            client.close()
            raise
        try:
            yield _SidecarSession(client, self.timeout)
        finally:
            client.close()
