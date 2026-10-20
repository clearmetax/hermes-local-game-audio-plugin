import json
import os
from pathlib import Path

import pytest
from hlga_under_test import errors as E
from hlga_under_test.game_adapters import GodotAdapter, detect_engine


def _gen(call, demo, name="enemy_hit"):
    r = call("generate_sound", {"description": "punchy hit", "duration_seconds": 0.3, "project_path": str(demo), "filename": name, "backend": "procedural"})
    assert r["success"], r
    return r["engine_ref"]


# ---------------------------------------------------------------- detection
def _mk(tmp_path, files):
    root = tmp_path / "p"
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return root


@pytest.mark.parametrize(
    "files,engine",
    [
        ({"project.godot": "config_version=5\n"}, "godot"),
        ({"ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.10f1\n", "Assets/.keep": ""}, "unity"),
        ({"MyGame.uproject": '{"EngineAssociation": "5.4"}'}, "unreal"),
        ({"package.json": '{"dependencies": {"phaser": "^3.80.0"}}'}, "web"),
        ({"game.project": ""}, "defold"),
        ({"Cargo.toml": '[dependencies]\nbevy = "0.14"\n'}, "bevy"),
        ({"main.lua": "function love.load() end"}, "love2d"),
        ({"game.py": "import pygame\n"}, "pygame"),
        ({"Game.yyp": "{}"}, "gamemaker"),
        ({"README.md": "hi"}, "unknown"),
        ({"package.json": '{"dependencies": {"react": "18"}}'}, "unknown"),
    ],
)
def test_detect(tmp_path, files, engine):
    _, info = detect_engine(_mk(tmp_path, files))
    assert info["engine"] == engine


def test_godot3_detected(tmp_path):
    _, info = detect_engine(_mk(tmp_path, {"project.godot": "config_version=4\n"}))
    assert info["version"] == "3.x"


# ---------------------------------------------------------------- inspection
def test_inspect_demo_finds_gaps(call, demo):
    r = call("inspect_game_audio", {"project_path": str(demo)})
    assert r["success"] and r["engine"]["engine"] == "godot" and r["read_only"]
    assert r["audio_asset_count"] == 0 and r["suggested_audio_dir"] == "audio/sfx"
    funcs = {(g["file"], g["function"]) for g in r["candidate_gaps"]}
    assert ("scripts/enemy.gd", "take_damage") in funcs and ("scripts/enemy.gd", "die") in funcs
    assert ("scripts/enemy.gd", "_ready") not in funcs


def test_inspect_references_and_missing(call, demo):
    ref = _gen(call, demo, "boom")
    (demo / "scripts" / "uses.gd").write_text(f'extends Node\nconst A = preload("{ref}")\nconst B = preload("res://audio/gone.ogg")\n')
    (demo / "audio" / "sfx" / "orphan.wav").write_bytes((demo / "audio/sfx/boom.wav").read_bytes())
    r = call("inspect_game_audio", {"project_path": str(demo)})
    by = {a["path"]: a for a in r["audio_assets"]}
    assert by["audio/sfx/boom.wav"]["referenced_by"] == ["scripts/uses.gd"]
    assert "audio/sfx/orphan.wav" in r["unreferenced_assets"]
    assert {"reference": "res://audio/gone.ogg", "in_file": "scripts/uses.gd"} in r["missing_references"]


def test_inspect_is_read_only(call, demo):
    before = {p: p.stat().st_mtime_ns for p in demo.rglob("*")}
    call("inspect_game_audio", {"project_path": str(demo)})
    assert {p: p.stat().st_mtime_ns for p in demo.rglob("*")} == before


def test_inspect_malformed_wav_reported(call, demo):
    (demo / "audio").mkdir()
    (demo / "audio" / "broken.wav").write_bytes(b"RIFF....nope")
    r = call("inspect_game_audio", {"project_path": str(demo)})
    assert r["audio_assets"][0]["problem"]


def test_unity_guid_references(tmp_path):
    root = _mk(tmp_path, {
        "ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.1f1\n",
        "Assets/Audio/hit.wav": "",
        "Assets/Audio/hit.wav.meta": "fileFormatVersion: 2\nguid: 0123456789abcdef0123456789abcdef\n",
        "Assets/Scenes/Main.unity": "m_audioClip: {fileID: 8300000, guid: 0123456789abcdef0123456789abcdef, type: 3}\n",
    })
    adapter, _ = detect_engine(root)
    inv = adapter.inventory()
    assert inv["audio_assets"][0]["referenced_by"] == ["Assets/Scenes/Main.unity"]


def test_unity_naming_and_folder_convention(call, tmp_path):
    root = _mk(tmp_path, {"ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.1f1\n", "Assets/Sounds/SFX/PlayerJump.wav": ""})
    r = call("generate_sound", {"description": "coin pickup", "project_path": str(root), "filename": "coin pickup", "backend": "procedural"})
    assert r["project_path"] == "Assets/Sounds/SFX/CoinPickup.wav", r


# ---------------------------------------------------------------- prepare
def test_prepare_dry_run_and_reuse(call, demo):
    _gen(call, demo, "enemy_hit")
    sounds = [{"name": "enemy_hit", "description": "hit"}, {"name": "enemy_death", "description": "defeat", "duration_seconds": 0.5}]
    dry = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": sounds, "dry_run": True, "backend": "procedural"})
    assert [p["action"] for p in dry["plan"]] == ["reuse", "generate"]
    assert not (demo / "audio/sfx/enemy_death.wav").exists()
    r = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": sounds, "backend": "procedural"})
    assert [(a["name"], a["status"]) for a in r["assets"]] == [("enemy_hit", "reused"), ("enemy_death", "generated")]
    lines = Path(r["manifest"]).read_text().strip().splitlines()
    assert json.loads(lines[-1])["tool"] == "prepare_game_audio"


def test_prepare_validation(call, demo):
    bad = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": [{"name": "a", "description": "x"}, {"name": "A", "description": "y"}]})
    assert bad["error"]["code"] == E.INVALID_INPUT
    many = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": [{"name": f"s{i}", "description": "x"} for i in range(13)]})
    assert many["error"]["code"] == E.INVALID_INPUT


def test_prepare_backend_missing_reports_cleanly(call, demo):
    r = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": [{"name": "x", "description": "x"}]})
    assert r["success"] is False and r["error"]["code"] == E.BACKEND_NOT_CONFIGURED


# ---------------------------------------------------------------- integrate
def _integ(call, demo, ref, **kw):
    args = {"project_path": str(demo), "sound": ref, "script": "res://scripts/enemy.gd", "function": "take_damage", "run_engine_checks": False}
    args.update(kw)
    return call("integrate_game_audio", args)


def test_preview_does_not_write(call, demo):
    ref = _gen(call, demo)
    before = (demo / "scripts/enemy.gd").read_bytes()
    r = _integ(call, demo, ref, action="preview")
    assert r["status"] == "preview" and "+const SFX_ENEMY_HIT" in r["diff"]
    assert (demo / "scripts/enemy.gd").read_bytes() == before


def test_apply_idempotent_and_revert(call, demo):
    ref = _gen(call, demo)
    original = (demo / "scripts/enemy.gd").read_bytes()
    r = _integ(call, demo, ref, action="apply", anchor="health -= amount")
    assert r["applied"] and r["validation"]["level"] == "static_checked"
    assert "unverified" in r["validation"]["playback"]
    text = (demo / "scripts/enemy.gd").read_text()
    lines = text.splitlines()
    i = lines.index("\thealth -= amount")
    assert lines[i + 1] == "\t_hlga_play_sfx_2d(SFX_ENEMY_HIT, 0.0, 0.05)"
    assert text.count("func _hlga_play_sfx_2d(") == 1
    assert _integ(call, demo, ref, action="apply", anchor="health -= amount")["status"] == "already_integrated"
    rv = call("integrate_game_audio", {"project_path": str(demo), "action": "revert", "integration_id": r["integration_id"]})
    assert rv["status"] == "reverted"
    assert (demo / "scripts/enemy.gd").read_bytes() == original
    assert (demo / "audio/sfx/enemy_hit.wav").is_file()  # assets are kept


def test_revert_conflict_when_edited(call, demo):
    ref = _gen(call, demo)
    r = _integ(call, demo, ref, action="apply")
    f = demo / "scripts/enemy.gd"
    f.write_text(f.read_text() + "\n# user edit\n")
    rv = call("integrate_game_audio", {"project_path": str(demo), "action": "revert", "integration_id": r["integration_id"]})
    assert rv["error"]["code"] == E.CONFLICT and "# user edit" in f.read_text()
    forced = call("integrate_game_audio", {"project_path": str(demo), "action": "revert", "integration_id": r["integration_id"], "force": True})
    assert forced["status"] == "reverted"


def test_revert_bad_id(call, demo):
    r = call("integrate_game_audio", {"project_path": str(demo), "action": "revert", "integration_id": "../../etc"})
    assert r["error"]["code"] == E.INVALID_INPUT


def test_function_not_found_lists_functions(call, demo):
    ref = _gen(call, demo)
    r = _integ(call, demo, ref, action="apply", function="explode")
    assert r["error"]["code"] == E.INTEGRATION_FAILED and "take_damage" in r["error"]["details"]["functions"]


def test_global_mode_and_second_sound_share_helper(call, demo):
    a = _gen(call, demo, "enemy_hit")
    b = _gen(call, demo, "enemy_death")
    _integ(call, demo, a, action="apply", mode="global")
    _integ(call, demo, b, action="apply", mode="global", function="die")
    text = (demo / "scripts/enemy.gd").read_text()
    assert text.count("func _hlga_play_sfx(") == 1 and "AudioStreamPlayer.new()" in text
    assert "_hlga_play_sfx(SFX_ENEMY_DEATH" in text


def test_rejects_non_node_and_static_and_oneliner(call, demo):
    ref = _gen(call, demo)
    (demo / "res.gd").write_text("extends Resource\nfunc hit():\n\tpass\n")
    assert _integ(call, demo, ref, script="res.gd", function="hit", action="apply")["error"]["code"] == E.INTEGRATION_FAILED
    (demo / "n.gd").write_text("extends Node\nstatic func hit():\n\tpass\nfunc jump(): pass\n")
    assert "static" in _integ(call, demo, ref, script="n.gd", function="hit", action="apply")["error"]["message"]
    assert "one-line" in _integ(call, demo, ref, script="n.gd", function="jump", action="apply")["error"]["message"]


def test_spaces_and_crlf_preserved(call, demo):
    ref = _gen(call, demo)
    src = "extends Node\r\n\r\nfunc hit(amount: int,\r\n        crit := false) -> void:\r\n    print(amount)\r\n"
    (demo / "s.gd").write_bytes(src.encode())
    r = _integ(call, demo, ref, script="s.gd", function="hit", action="apply")
    assert r["applied"], r
    out = (demo / "s.gd").read_bytes().decode()
    assert "\r\n    _hlga_play_sfx(SFX_ENEMY_HIT, 0.0, 0.05)\r\n    print(amount)" in out
    assert "\n" not in out.replace("\r\n", "")
    assert "\t" not in out


def test_pass_only_body(call, demo):
    ref = _gen(call, demo)
    (demo / "p.gd").write_text("extends Node\n\nfunc on_pickup():\n\tpass\n")
    _integ(call, demo, ref, script="p.gd", function="on_pickup", action="apply")
    assert "func on_pickup():\n\t_hlga_play_sfx(SFX_ENEMY_HIT, 0.0, 0.05)\n\tpass" in (demo / "p.gd").read_text()


def test_sound_outside_project_rejected(call, demo, tmp_path):
    r = _integ(call, demo, str(tmp_path / "x.wav"), action="apply")
    assert r["error"]["code"] == E.PATH_REJECTED


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_symlinked_script_rejected(call, demo, tmp_path):
    ref = _gen(call, demo)
    outside = tmp_path / "evil.gd"
    outside.write_text("extends Node\nfunc take_damage():\n\tpass\n")
    (demo / "link.gd").symlink_to(outside)
    r = _integ(call, demo, ref, script="link.gd", action="apply")
    assert r["error"]["code"] == E.PATH_REJECTED and "pass" in outside.read_text()


def test_godot3_unsupported(call, demo):
    ref = _gen(call, demo)
    pf = demo / "project.godot"
    pf.write_text(pf.read_text().replace("config_version=5", "config_version=4").replace('"4.3", ', ""))
    assert _integ(call, demo, ref, action="apply")["error"]["code"] == E.UNSUPPORTED_ENGINE


def test_unity_returns_snippet_not_edit(call, tmp_path):
    root = _mk(tmp_path, {"ProjectSettings/ProjectVersion.txt": "m_EditorVersion: 2022.3.1f1\n", "Assets/Scripts/Enemy.cs": "class Enemy {}"})
    g = call("generate_sound", {"description": "hit", "project_path": str(root), "backend": "procedural", "filename": "hit"})
    r = call("integrate_game_audio", {"project_path": str(root), "action": "apply", "sound": g["project_path"], "function": "TakeDamage"})
    assert r["success"] and r["partial"] and r["applied"] is False and "PlayClipAtPoint" in r["csharp_snippet"]
    assert (root / "Assets/Scripts/Enemy.cs").read_text() == "class Enemy {}"
    assert not list(root.rglob("*.meta"))


def test_plan_edit_unit_existing_const_reused():
    a = GodotAdapter(Path("."))
    src = 'extends Node2D\nconst BOOM := preload("res://a.wav")\n\nfunc explode():\n\tqueue_free()\n'
    plan = a.plan_script_edit(src, function="explode", asset_ref="res://a.wav")
    assert plan["const"] == "BOOM" and plan["text"].count("preload(") == 1
