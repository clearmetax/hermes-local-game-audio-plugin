"""Backend interface: Hermes tools never touch model-specific code directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class GenerationRequest:
    prompt: str
    seconds: float
    output_path: Path  # Plugin-owned render path; must not exist yet.
    seed: int | None = None
    # kind, bpm, key, time_signature, lyrics, negative_prompt, steps, cfg ... (backend-dependent)
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackendStatus:
    name: str
    configured: bool
    ready: bool
    is_ai_model: bool
    details: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    setup_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "ready": self.ready,
            "is_ai_model": self.is_ai_model,
            "details": self.details,
            "problems": self.problems,
            "setup_steps": self.setup_steps,
        }


class Session(ABC):
    """One connection to a backend, reused for several generations."""

    @abstractmethod
    def generate(self, request: GenerationRequest) -> Path: ...


class AudioBackend(ABC):
    name: str = "base"
    is_ai_model: bool = True
    # What the backend can honour natively; everything else is achieved through
    # prompting + post-processing and is reported as such.
    supports: dict[str, Any] = {"sfx": True, "music": True, "ambience": True, "bpm": False, "key": False,
                                 "seed": False, "lyrics": False, "negative_prompt": False, "max_seconds": 0}

    def supports_for(self, kind: str) -> dict[str, Any]:
        return dict(self.supports)

    @abstractmethod
    def status(self, *, deep: bool = False) -> BackendStatus: ...

    @abstractmethod
    @contextmanager
    def session(self) -> Iterator[Session]: ...
