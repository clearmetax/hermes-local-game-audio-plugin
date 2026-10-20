"""Backend selection. All inference is local; there is no cloud fallback."""

from __future__ import annotations

from typing import Callable

from .. import errors as E
from ..settings import Settings
from . import agent_audio as _aa
from .agent_audio import AgentAudioBackend
from .base import AudioBackend, BackendStatus, GenerationRequest, Session
from .comfyui import ComfyUIBackend
from .procedural import ProceduralBackend

BACKENDS = ("auto", "agent_audio", "comfyui", "procedural")
MUSIC_KINDS = ("music", "stinger", "ambience")


def _comfy(settings: Settings) -> ComfyUIBackend:
    from ..codecs_io import find_ffmpeg

    v = settings.values
    return ComfyUIBackend(url=v["comfyui_url"], workflow_sfx=v["comfyui_workflow_sfx"], workflow_music=v["comfyui_workflow_music"],
                          allow_remote=bool(v["comfyui_allow_remote"]), timeout=settings.timeout,
                          ffmpeg=find_ffmpeg(v["ffmpeg_executable"]))


def resolve_choice(settings: Settings, override: str | None = None, kind: str = "sfx") -> str:
    v = settings.values
    choice = (override or "").strip().lower()
    if not choice and kind in MUSIC_KINDS and (v.get("music_backend") or "auto") != "auto":
        choice = v["music_backend"]
    choice = choice or (v["backend"] or "auto").strip().lower()
    if choice not in BACKENDS:
        raise E.AudioPluginError(E.INVALID_INPUT, f"Unknown backend '{choice}'. Use one of {list(BACKENDS)}")
    if choice != "auto":
        return choice
    # auto: a configured ComfyUI workflow for this kind > Agent Audio. Never the
    # procedural synth: placeholder audio must be chosen explicitly.
    wf = v["comfyui_workflow_music"] if kind in MUSIC_KINDS else v["comfyui_workflow_sfx"]
    any_wf = bool(v["comfyui_workflow_sfx"] or v["comfyui_workflow_music"])
    if wf or (any_wf and not _aa.find_checkout(v["agent_audio_checkout"])):
        return "comfyui"
    return "agent_audio"


def make_backend(settings: Settings, *, override: str | None = None, call_mcp: Callable[..., dict] | None = None,
                 kind: str = "sfx") -> AudioBackend:
    choice = resolve_choice(settings, override, kind)
    if choice == "procedural":
        return ProceduralBackend()
    if choice == "comfyui":
        return _comfy(settings)
    return AgentAudioBackend(
        checkout=settings.values["agent_audio_checkout"],
        agent_audio_home=settings.values["agent_audio_home"],
        transport=settings.values["agent_audio_transport"],
        mcp_server=settings.values["agent_audio_mcp_server"],
        call_mcp=call_mcp,
        timeout=settings.timeout,
    )


__all__ = [
    "AudioBackend", "BackendStatus", "GenerationRequest", "Session",
    "AgentAudioBackend", "ComfyUIBackend", "ProceduralBackend", "make_backend", "resolve_choice", "BACKENDS", "MUSIC_KINDS",
]
