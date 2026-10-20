"""Structured error type shared by every tool.

Each failure carries a stable ``code`` so Hermes (and the bundled skill) can
tell setup problems apart from generation or integration failures.
"""

from __future__ import annotations

from typing import Any

# Stable error codes.
INVALID_INPUT = "INVALID_INPUT"
BACKEND_NOT_CONFIGURED = "BACKEND_NOT_CONFIGURED"
BACKEND_NOT_READY = "BACKEND_NOT_READY"
BACKEND_ERROR = "BACKEND_ERROR"
GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
INVALID_OUTPUT = "INVALID_OUTPUT"
PATH_REJECTED = "PATH_REJECTED"
FILE_EXISTS = "FILE_EXISTS"
OUT_OF_SPACE = "OUT_OF_SPACE"
PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
UNSUPPORTED_ENGINE = "UNSUPPORTED_ENGINE"
INTEGRATION_FAILED = "INTEGRATION_FAILED"
CONFLICT = "CONFLICT"
INTERNAL_ERROR = "INTERNAL_ERROR"


class AudioPluginError(Exception):
    def __init__(self, code: str, message: str, *, hint: str | None = None, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"success": False, "error": {"code": self.code, "message": self.message}}
        if self.hint:
            out["error"]["hint"] = self.hint
        if self.details:
            out["error"]["details"] = self.details
        return out
