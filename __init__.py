"""Hermes Local Game Audio — native Hermes plugin entry point.

Registration is deliberately lightweight: no ML frameworks are imported and
nothing is downloaded or started here. The model server is launched only when
a generation tool actually runs.
"""

from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path

from . import schemas, tools
from .settings import PLUGIN_NAME

logger = logging.getLogger(__name__)

TOOLSET = "game_audio"
SKILL_NAME = "game-audio"
_EMOJI = {
    "audio_status": "🔊", "generate_sound": "💥", "generate_music": "🎵", "generate_variations": "🎚️",
    "analyze_audio": "📈", "process_audio": "🎛️",
    "inspect_game_audio": "🔎", "prepare_game_audio": "🎮", "integrate_game_audio": "🔧",
}
_GAME_WORDS = re.compile(
    r"\b(game|godot|unity|unreal|gdscript|phaser|pixi|defold|gamemaker|bevy|pygame|love2d|sfx|sound ?effects?|"
    r"enemy|player character|platformer|shooter|level design|hitbox|power-?up|soundtrack|game music|bgm)\b",
    re.I,
)


def _make_hint_hook(rt: tools.Runtime):
    seen: set[str] = set()

    def pre_llm_call(session_id: str = "", user_message: str = "", **kwargs):
        try:
            if not rt.settings().values.get("inject_skill_hint", True):
                return None
            if not user_message or session_id in seen or not _GAME_WORDS.search(user_message):
                return None
            seen.add(session_id)
            if len(seen) > 5000:
                seen.clear()
            return {
                "context": (
                    f"[{PLUGIN_NAME}] Local game sound-effect and music tools are available (toolset '{TOOLSET}'). "
                    f"If this game task needs audio, load skill_view('{PLUGIN_NAME}:{SKILL_NAME}') first. "
                    "Reuse existing project audio, and add no sound where the user wants silence."
                )
            }
        except Exception:  # hooks must never break a turn
            return None

    return pre_llm_call


def _make_command(rt: tools.Runtime):
    def game_audio_command(raw_args: str = "", **kwargs) -> str:
        words = (raw_args or "").split()
        deep = "deep" in words or "--deep" in words
        data = json.loads(tools.audio_status(rt, {"deep_check": deep}))
        if not data.get("success"):
            return f"game-audio: {data['error']['message']}"
        r = data["routing"]
        lines = [f"Hermes Local Game Audio — SFX via {r['sfx']}, music via {r['music']}"]
        for name in ("agent_audio", "comfyui"):
            b = data["backends"][name]
            ready = b["ready"] if deep else "not checked (use /game-audio deep)"
            lines.append(f"  {name}: configured={b['configured']} ready={ready}")
            lines += [f"    problem: {p}" for p in b["problems"] if deep or "not checked" not in p and "not contacted" not in p]
            if not b["configured"]:
                lines += [f"    setup: {st}" for st in b["setup_steps"][:6]]
        lines.append("  procedural placeholder synth: ready")
        q = data["quality_tools"]
        lines.append(f"  CLAP prompt-match: {'on' if q['clap_prompt_match']['configured'] else 'off'} | ffmpeg: {q['ffmpeg'] or 'not found'}")
        lines.append(f"  Godot: {data['godot_executable'] or 'not found'}")
        lines += [f"  warning: {w}" for w in data["machine"]["warnings"]]
        return "\n".join(lines)

    return game_audio_command


def register(ctx) -> None:
    call_mcp = getattr(ctx, "call_mcp", None)
    get_config = getattr(ctx, "get_config", None)
    rt = tools.Runtime(get_config=get_config, call_mcp=call_mcp)

    for schema in schemas.ALL:
        name = schema["name"]
        handler = functools.partial(tools.HANDLERS[name], rt)
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            description=schema["description"].split(".")[0],
            emoji=_EMOJI.get(name, ""),
        )

    skills_dir = Path(__file__).parent / "skills"
    if hasattr(ctx, "register_skill") and skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.is_file():
                ctx.register_skill(child.name, skill_md)

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_llm_call", _make_hint_hook(rt))

    if hasattr(ctx, "register_command"):
        try:
            ctx.register_command(
                "game-audio",
                _make_command(rt),
                description="Show local game-audio backend status (add 'deep' to verify the model runtime)",
            )
        except Exception:  # older hosts / name clash: the tools still work
            logger.debug("game-audio slash command not registered", exc_info=True)
