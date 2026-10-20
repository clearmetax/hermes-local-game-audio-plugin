"""Minimal, dependency-free MCP client for a local stdio server.

Implements the subset of the Model Context Protocol needed here:
``initialize`` -> ``notifications/initialized`` -> ``tools/list`` / ``tools/call``.
Messages are newline-delimited JSON-RPC 2.0, as specified for the stdio
transport. The server runs as an isolated child process (its own Python
environment), launched from an argument array, never a shell string.
"""

from __future__ import annotations

import collections
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..safety import child_env

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "hermes-local-game-audio", "version": "0.1.0"}


class McpError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, stderr_tail: str = ""):
        super().__init__(message)
        self.code = code
        self.stderr_tail = stderr_tail


class McpTimeout(McpError):
    pass


class StdioMcpClient:
    def __init__(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        startup_timeout: float = 60.0,
    ):
        if not argv or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a non-empty list of strings")
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.startup_timeout = startup_timeout
        self.proc: subprocess.Popen[bytes] | None = None
        self._next_id = 1
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=40)
        self._write_lock = threading.Lock()
        self.server_info: dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            self.argv,
            cwd=str(self.cwd) if self.cwd else None,
            env=self.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        result = self.request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
            timeout=self.startup_timeout,
        )
        self.server_info = result.get("serverInfo", {}) if isinstance(result, dict) else {}
        self.notify("notifications/initialized", {})

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        self.proc = None
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name == "nt":
            subprocess.run(  # noqa: S603,S607 - fixed argv
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(proc.pid, 9)
            except OSError:
                proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass

    def __enter__(self) -> "StdioMcpClient":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- io ----------------------------------------------------------------
    def _read_stdout(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                self._stderr.append("[non-JSON stdout line ignored]")
                continue
            if isinstance(msg, dict):
                self._messages.put(msg)
        self._messages.put(None)  # EOF marker

    def _read_stderr(self) -> None:
        proc = self.proc
        assert proc is not None and proc.stderr is not None
        for line in proc.stderr:
            self._stderr.append(line.decode("utf-8", "replace").rstrip()[:500])

    def stderr_tail(self, lines: int = 12) -> str:
        return "\n".join(list(self._stderr)[-lines:])

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise McpError("MCP server is not running", stderr_tail=self.stderr_tail())
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except OSError as exc:
                raise McpError(f"MCP server pipe closed: {exc}", stderr_tail=self.stderr_tail()) from exc

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], *, timeout: float) -> Any:
        req_id = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise McpTimeout(f"MCP {method} timed out after {timeout:.0f}s", stderr_tail=self.stderr_tail())
            try:
                msg = self._messages.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if msg is None:
                raise McpError("MCP server exited unexpectedly", stderr_tail=self.stderr_tail())
            if "method" in msg and "id" in msg:  # server -> client request
                self._answer_server_request(msg)
                continue
            if "method" in msg:  # notification (logging/progress); ignore
                continue
            if msg.get("id") != req_id:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise McpError(str(err.get("message", "MCP error")), code=err.get("code"), stderr_tail=self.stderr_tail())
            return msg.get("result")

    def _answer_server_request(self, msg: dict[str, Any]) -> None:
        if msg.get("method") == "ping":
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        else:
            self._send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "error": {"code": -32601, "message": "Method not supported by this client"},
            })

    # -- tools -------------------------------------------------------------
    def list_tools(self, timeout: float = 30.0) -> list[dict[str, Any]]:
        result = self.request("tools/list", {}, timeout=timeout) or {}
        return list(result.get("tools", []))

    def call_tool(self, name: str, arguments: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        """Return {"is_error": bool, "text": str, "structured": Any}."""
        result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout) or {}
        texts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict) and c.get("type") == "text"]
        return {
            "is_error": bool(result.get("isError")),
            "text": "\n".join(texts).strip(),
            "structured": result.get("structuredContent"),
        }


def python_in_venv(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def isolated_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a child Python that must not inherit Hermes's interpreter state."""
    env = child_env()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONSTARTUP"):
        env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if extra:
        env.update(extra)
    return env


__all__ = ["StdioMcpClient", "McpError", "McpTimeout", "python_in_venv", "isolated_env"]
