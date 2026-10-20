"""Godot adapter (first-class target).

Detection: ``project.godot`` at the root (``config_version=5`` => Godot 4.x,
``4`` => Godot 3.x). Script integration targets Godot 4 GDScript.

Integration strategy (minimal, reviewable, reversible):
1. add ``const SFX_<NAME> := preload("res://...wav")`` after the script header;
2. append one small one-shot player helper (``_hlga_play_sfx[_2d|_3d]``) once
   per script. The player is parented to the current scene, so the sound keeps
   playing even if the emitting node frees itself (e.g. an enemy dying);
3. insert a single call at the top of the chosen function (or after an anchor line).
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .. import errors as E
from ..safety import child_env
from .base import EngineAdapter
from .scan import read_text

NON_NODE_BASES = {"Resource", "RefCounted", "Reference", "Object", "MainLoop", "SceneTree", "EditorScript"}
HELPER_MARK = "# --- hermes-local-game-audio: audio helper (safe to edit) ---"


def find_godot(configured: str = "") -> str | None:
    for raw in (configured, os.environ.get("GODOT", ""), os.environ.get("GODOT_BIN", "")):
        if raw and Path(raw).expanduser().is_file():
            return str(Path(raw).expanduser())
    for name in ("godot", "godot4", "Godot", "godot-mono", "godot4-mono"):
        hit = shutil.which(name)
        if hit:
            return hit
    home = Path.home()
    patterns = [
        str(home / "Downloads" / "Godot_v4*" / "Godot_v4*.exe"),
        str(home / "Downloads" / "Godot_v4*.exe"),
        str(home / "Desktop" / "Godot_v4*.exe"),
        str(home / "OneDrive" / "Desktop" / "Godot_v4*.exe"),
        str(home / "scoop" / "apps" / "godot" / "current" / "godot.exe"),
        "C:/Program Files/Godot*/Godot*.exe",
        "C:/Program Files (x86)/Steam/steamapps/common/Godot Engine/godot.windows.opt.tools.64.exe",
        "/Applications/Godot.app/Contents/MacOS/Godot",
        str(home / "Applications" / "Godot.app" / "Contents" / "MacOS" / "Godot"),
    ]
    for pat in patterns:
        hits = sorted(h for h in glob.glob(pat) if "console" not in h.lower())
        if hits:
            return hits[-1]
    return None


class GodotAdapter(EngineAdapter):
    name = "godot"
    display_name = "Godot"
    default_audio_dir = "audio/sfx"
    reference_exts = {".gd", ".cs", ".tscn", ".tres", ".godot", ".cfg"}
    supports_code_integration = True

    @classmethod
    def detect(cls, root: Path) -> dict[str, Any] | None:
        pf = root / "project.godot"
        if not pf.is_file():
            return None
        text = read_text(pf) or ""
        m = re.search(r"^config_version\s*=\s*(\d+)", text, re.M)
        cv = int(m.group(1)) if m else None
        name = re.search(r'^config/name\s*=\s*"([^"]*)"', text, re.M)
        feats = re.search(r'^config/features\s*=\s*PackedStringArray\(([^)]*)\)', text, re.M)
        version = "4.x" if cv == 5 else ("3.x" if cv == 4 else "unknown")
        if feats:
            vm = re.search(r'"(4\.\d+)"', feats.group(1))
            if vm:
                version = vm.group(1)
        buses = (root / "default_bus_layout.tres").is_file()
        return {
            "engine": "godot",
            "version": version,
            "project_name": name.group(1) if name else None,
            "marker": "project.godot",
            "csharp": any(root.glob("*.csproj")),
            "has_bus_layout": buses,
        }

    def engine_ref(self, rel: str) -> str:
        return "res://" + rel

    def rel_from_ref(self, ref: str) -> str:
        return ref[6:] if ref.startswith("res://") else ref

    def find_references(self, audio, texts):  # noqa: ANN001 - same contract as base
        refs: dict[str, list[str]] = {}
        for item in audio:
            ref = item["ref"]
            refs[item["path"]] = [rel for rel, t in texts.items() if ref in t]
        return refs

    def find_missing(self, texts, existing):  # noqa: ANN001
        missing = []
        pat = re.compile(r'res://([^"\'\s)]+\.(?:wav|ogg|mp3))', re.I)
        for rel, t in texts.items():
            for m in pat.finditer(t):
                if m.group(1) not in existing and not (self.root / m.group(1)).exists():
                    missing.append({"reference": "res://" + m.group(1), "in_file": rel})
        return missing[:100]

    def inventory(self) -> dict[str, Any]:
        inv = super().inventory()
        players = []
        for p in sorted(self.root.rglob("*.tscn")):
            if any(part.startswith(".") for part in p.relative_to(self.root).parts):
                continue
            t = read_text(p) or ""
            for m in re.finditer(r'\[node name="([^"]+)" type="(AudioStreamPlayer(?:2D|3D)?)"', t):
                players.append({"scene": p.relative_to(self.root).as_posix(), "node": m.group(1), "type": m.group(2)})
        inv["audio_stream_players"] = players[:100]
        inv["godot_executable"] = None
        return inv

    # ------------------------------------------------------------------ edit
    def plan_script_edit(
        self,
        script_text: str,
        *,
        function: str,
        asset_ref: str,
        mode: str = "auto",
        volume_db: float = 0.0,
        pitch_variation: float = 0.0,
        anchor: str | None = None,
        fade_in: float = 1.0,
    ) -> dict[str, Any]:
        """Pure function: returns {"text": new_text, "changes": [...], "noop": bool}."""
        nl = "\r\n" if "\r\n" in script_text else "\n"
        lines = script_text.split(nl)
        indent_unit = "\t"
        for ln in lines:
            m = re.match(r"^( +)\S", ln)
            if m:
                indent_unit = m.group(1) if len(m.group(1)) in (2, 4) else "    "
                break
            if ln.startswith("\t"):
                break
        ext = re.search(r"^extends\s+([A-Za-z_][\w.]*|\"[^\"]+\")", script_text, re.M)
        base = ext.group(1).strip('"') if ext else "RefCounted"
        if base in NON_NODE_BASES:
            raise E.AudioPluginError(E.INTEGRATION_FAILED, f"Script extends {base}, which is not a Node; pick a Node script for playback.")
        if mode == "auto":
            mode = "2d" if base.endswith("2D") else ("3d" if base.endswith("3D") else "global")
        if mode not in ("global", "2d", "3d", "music", "ambience"):
            raise E.AudioPluginError(E.INVALID_INPUT, "mode must be auto, global, 2d, 3d, music or ambience")
        if mode in ("2d", "3d") and not ext:
            raise E.AudioPluginError(E.INVALID_INPUT, "Positional playback needs a script that extends a 2D/3D node")
        helper_name = {"global": "_hlga_play_sfx", "2d": "_hlga_play_sfx_2d", "3d": "_hlga_play_sfx_3d",
                       "music": "_hlga_play_music", "ambience": "_hlga_play_ambience"}[mode]
        player_cls = {"global": "AudioStreamPlayer", "2d": "AudioStreamPlayer2D", "3d": "AudioStreamPlayer3D",
                      "music": "AudioStreamPlayer", "ambience": "AudioStreamPlayer"}[mode]
        changes: list[str] = []

        # 1. constant ---------------------------------------------------------
        stem = Path(asset_ref).stem
        prefix = {"music": "MUSIC_", "ambience": "AMB_"}.get(mode, "SFX_")
        const_name = prefix + re.sub(r"[^A-Z0-9]+", "_", stem.upper()).strip("_")
        existing_const = re.search(r'^const\s+(\w+)\s*:?=\s*preload\(\s*"' + re.escape(asset_ref) + r'"\s*\)', script_text, re.M)
        if existing_const:
            const_name = existing_const.group(1)
        else:
            if re.search(r"^const\s+" + const_name + r"\b", script_text, re.M):
                n = 2
                while re.search(r"^const\s+" + f"{const_name}_{n}" + r"\b", script_text, re.M):
                    n += 1
                const_name = f"{const_name}_{n}"
            insert_at = 0
            for i, ln in enumerate(lines):
                s = ln.strip()
                if s.startswith(("@tool", "extends", "class_name", "@icon")) or (i < 3 and s.startswith("##")):
                    insert_at = i + 1
                elif s and not s.startswith("#"):
                    break
            new_line = f'const {const_name} := preload("{asset_ref}")'
            lines.insert(insert_at, new_line)
            changes.append(f"added `{new_line}`")

        # 2. call inside function ---------------------------------------------
        func_re = re.compile(r"^(?P<ind>[ \t]*)(?:static\s+)?func\s+" + re.escape(function) + r"\s*\(")
        start = next((i for i, ln in enumerate(lines) if func_re.match(ln)), None)
        if start is None:
            names = sorted({m.group(1) for m in re.finditer(r"^\s*func\s+(\w+)", script_text, re.M)})
            raise E.AudioPluginError(E.INTEGRATION_FAILED, f"Function '{function}' not found", functions=names[:80])
        if lines[start].lstrip().startswith("static"):
            raise E.AudioPluginError(E.INTEGRATION_FAILED, f"'{function}' is static; it cannot play audio through the node")
        func_indent = func_re.match(lines[start]).group("ind")  # type: ignore[union-attr]
        code0 = _strip_comment(lines[start])
        if re.search(r"\)\s*(?:->\s*[\w\[\]., ]+)?\s*:\s*\S", code0):
            raise E.AudioPluginError(E.INTEGRATION_FAILED, f"'{function}' is a one-line function; expand it to a block first")
        sig_end = start
        while not _strip_comment(lines[sig_end]).rstrip().endswith(":"):
            sig_end += 1
            if sig_end >= len(lines) or sig_end - start > 20:
                raise E.AudioPluginError(E.INTEGRATION_FAILED, f"Could not parse the signature of '{function}'")
        end = sig_end + 1
        body_indent = None
        while end < len(lines):
            ln = lines[end]
            if ln.strip() == "":
                end += 1
                continue
            ind = ln[: len(ln) - len(ln.lstrip())]
            if len(ind) <= len(func_indent):
                break
            if body_indent is None:
                body_indent = ind
            end += 1
        if body_indent is None:
            body_indent = func_indent + indent_unit
        body = lines[sig_end + 1:end]
        if mode in ("music", "ambience"):
            call = f"{helper_name}({const_name}, {float(volume_db):.1f}, {float(fade_in):.2f})"
        else:
            call = f"{helper_name}({const_name}, {float(volume_db):.1f}, {float(pitch_variation):.2f})"
        if any(f"{helper_name}({const_name}" in b for b in body):
            noop_call = True
        else:
            noop_call = False
            insert_line = sig_end + 1
            if anchor:
                hit = next((i for i, b in enumerate(body) if anchor in b), None)
                if hit is None:
                    raise E.AudioPluginError(E.INTEGRATION_FAILED, f"Anchor text not found in '{function}': {anchor!r}")
                anchor_line = body[hit]
                body_indent = anchor_line[: len(anchor_line) - len(anchor_line.lstrip())]
                insert_line = sig_end + 1 + hit + 1
            lines.insert(insert_line, body_indent + call)
            # a body consisting only of `pass` stays valid with the call before it
            changes.append(f"inserted `{call}` in func {function}()")

        # 3. helper -----------------------------------------------------------
        text = nl.join(lines)
        if mode in ("music", "ambience") and not re.search(r"^func\s+" + helper_name + r"\s*\(", text, re.M):
            text = text.rstrip("\r\n") + nl.join(_loop_helper(helper_name, mode, indent_unit))
            changes.append(f"added helper func {helper_name}() (persistent looping AudioStreamPlayer)")
        elif not re.search(r"^func\s+" + helper_name + r"\s*\(", text, re.M):
            i1, i2 = indent_unit, indent_unit * 2
            positional = mode in ("2d", "3d")
            immediate = [f"{i2}host.add_child(player)"]
            if positional:
                immediate.append(f"{i2}player.global_position = global_position")
            immediate.append(f"{i2}player.play()")
            deferred = [f"{i2}# The scene is still being set up (called from _ready): add it next frame.",
                        f"{i2}player.autoplay = true"]
            if positional:
                deferred += [f"{i2}var spawn_pos := global_position",
                             f"{i2}player.ready.connect(func() -> void: player.global_position = spawn_pos)"]
            deferred.append(f"{i2}host.add_child.call_deferred(player)")
            helper = [
                "",
                "",
                HELPER_MARK,
                f"func {helper_name}(stream: AudioStream, volume_db: float = 0.0, pitch_variation: float = 0.0) -> void:",
                f"{i1}if stream == null or not is_inside_tree():",
                f"{i2}return",
                f"{i1}var player := {player_cls}.new()",
                f"{i1}player.stream = stream",
                f"{i1}player.volume_db = volume_db",
                f"{i1}if pitch_variation > 0.0:",
                f"{i2}player.pitch_scale = randf_range(1.0 - pitch_variation, 1.0 + pitch_variation)",
                f'{i1}if AudioServer.get_bus_index(&"SFX") != -1:',
                f'{i2}player.bus = &"SFX"',
                f"{i1}player.finished.connect(player.queue_free)",
                f"{i1}var host: Node = get_tree().current_scene if get_tree().current_scene else get_tree().root",
                f"{i1}if host.is_node_ready():",
                *immediate,
                f"{i1}else:",
                *deferred,
                "",
            ]
            body_text = text.rstrip("\r\n")
            text = body_text + nl.join(helper)
            changes.append(f"added helper func {helper_name}() using {player_cls}")
        return {"text": text, "changes": changes, "noop": noop_call and not changes, "const": const_name, "helper": helper_name, "mode": mode}

    # ------------------------------------------------------------- validate
    def static_checks(self, script_text: str, asset_rel: str, helper: str) -> list[dict[str, Any]]:
        checks = []
        checks.append({"check": "asset exists in project", "ok": (self.root / asset_rel).is_file()})
        checks.append({"check": "helper defined exactly once", "ok": len(re.findall(r"^func\s+" + helper + r"\s*\(", script_text, re.M)) == 1})
        mixed = [i + 1 for i, ln in enumerate(script_text.splitlines()) if re.match(r"^(\t+ +| +\t+)\S", ln)]
        checks.append({"check": "no mixed tab/space indentation", "ok": not mixed, **({"lines": mixed[:10]} if mixed else {})})
        return checks

    def engine_checks(self, godot: str, script_rel: str, timeout: int = 180) -> dict[str, Any]:
        result: dict[str, Any] = {"godot_executable": godot}

        def run(args: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603 - argv list
                [godot, *args], cwd=str(self.root), capture_output=True, text=True, timeout=timeout, check=False,
                env=child_env(),
            )

        try:
            imp = run(["--headless", "--path", str(self.root), "--import"])
            if imp.returncode != 0 and "--import" in (imp.stderr + imp.stdout) and "unknown" in (imp.stderr + imp.stdout).lower():
                imp = run(["--headless", "--path", str(self.root), "--editor", "--quit"])
            result["import"] = {"returncode": imp.returncode, "output_tail": (imp.stdout + imp.stderr)[-1200:]}
            chk = run(["--headless", "--path", str(self.root), "--check-only", "--script", "res://" + script_rel])
            out = chk.stdout + chk.stderr
            errors = [ln for ln in out.splitlines() if "SCRIPT ERROR" in ln or "Parse Error" in ln or "ERROR:" in ln]
            result["script_check"] = {"returncode": chk.returncode, "ok": chk.returncode == 0 and not errors, "errors": errors[:20], "output_tail": out[-1200:]}
        except subprocess.TimeoutExpired:
            result["error"] = f"Godot did not finish within {timeout}s"
        except OSError as exc:
            result["error"] = f"Could not run Godot: {exc}"
        return result


def _loop_helper(name: str, mode: str, unit: str) -> list[str]:
    """A persistent, looping player on the calling node (one per mode); restarting
    with another stream crossfades. Loops WAV (forward loop over the whole file
    unless the WAV carries its own loop points), Ogg Vorbis and MP3 streams."""
    i1, i2, i3 = unit, unit * 2, unit * 3
    node = "HLGA_Music" if mode == "music" else "HLGA_Ambience"
    bus = "Music" if mode == "music" else "Ambience"
    return [
        "",
        "",
        HELPER_MARK,
        f"func {name}(stream: AudioStream, volume_db: float = 0.0, fade_in: float = 1.0) -> void:",
        f"{i1}if stream == null:",
        f"{i2}return",
        f"{i1}if stream is AudioStreamWAV:",
        f"{i2}var wav_stream := stream as AudioStreamWAV",
        f"{i2}if wav_stream.loop_mode == AudioStreamWAV.LOOP_DISABLED:",
        f"{i3}wav_stream.loop_mode = AudioStreamWAV.LOOP_FORWARD",
        f"{i3}wav_stream.loop_begin = 0",
        f"{i3}wav_stream.loop_end = int(wav_stream.get_length() * wav_stream.mix_rate)",
        f'{i1}elif "loop" in stream:',
        f'{i2}stream.set("loop", true)',
        f'{i1}var old := get_node_or_null(^"{node}") as AudioStreamPlayer',
        f"{i1}if old != null and old.stream == stream and old.playing:",
        f"{i2}return",
        f"{i1}if old != null:",
        f'{i2}old.name = "{node}_Old"',
        f"{i2}var out_tween := create_tween()",
        f'{i2}out_tween.tween_property(old, "volume_db", -60.0, max(fade_in, 0.05))',
        f"{i2}out_tween.tween_callback(old.queue_free)",
        f"{i1}var player := AudioStreamPlayer.new()",
        f'{i1}player.name = "{node}"',
        f"{i1}player.stream = stream",
        f'{i1}if AudioServer.get_bus_index(&"{bus}") != -1:',
        f'{i2}player.bus = &"{bus}"',
        f"{i1}player.volume_db = -60.0 if fade_in > 0.0 else volume_db",
        f"{i1}add_child(player)",
        f"{i1}player.play()",
        f"{i1}if fade_in > 0.0:",
        f"{i2}var in_tween := create_tween()",
        f'{i2}in_tween.tween_property(player, "volume_db", volume_db, fade_in)',
        "",
    ]


def _strip_comment(line: str) -> str:
    """Remove a GDScript ``#`` comment, ignoring ``#`` inside string literals."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#":
            return line[:i]
        i += 1
    return line
