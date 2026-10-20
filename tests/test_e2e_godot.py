"""End-to-end acceptance scenario against a real Godot binary.

Skipped unless a Godot 4 executable is found (PATH, GODOT env var, or
HLGA_GODOT_EXECUTABLE). Uses the procedural backend so it runs without model
weights; the AI-model path is covered by test_backend.py's real tests.
"""

import os
import subprocess

import pytest
from hlga_under_test.game_adapters import find_godot

GODOT = os.environ.get("HLGA_GODOT_EXECUTABLE") or find_godot()
pytestmark = pytest.mark.skipif(not GODOT, reason="Godot executable not found")


def _trigger(demo):
    out = subprocess.run([GODOT, "--headless", "--path", str(demo), "--script", "res://tests/trigger_test.gd"],
                         capture_output=True, text=True, timeout=120)
    return out.returncode, out.stdout + out.stderr


def test_enemy_hit_feedback_end_to_end(call, demo, monkeypatch):
    monkeypatch.setenv("HLGA_GODOT_EXECUTABLE", GODOT)
    subprocess.run([GODOT, "--headless", "--path", str(demo), "--import"], capture_output=True, timeout=180)
    code, log = _trigger(demo)
    assert code != 0 and "overall=FAIL" in log  # baseline: silent

    gaps = call("inspect_game_audio", {"project_path": str(demo)})["candidate_gaps"]
    assert any(g["function"] == "take_damage" for g in gaps)

    prep = call("prepare_game_audio", {
        "project_path": str(demo), "feature": "enemy hit + death feedback", "backend": "procedural",
        "sounds": [{"name": "enemy_hit", "description": "punchy hit", "duration_seconds": 0.4},
                   {"name": "enemy_death", "description": "defeat sound", "duration_seconds": 0.8}],
    })
    refs = {a["name"]: a["engine_ref"] for a in prep["assets"]}

    hit = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": refs["enemy_hit"],
                                        "script": "res://scripts/enemy.gd", "function": "take_damage", "anchor": "health -= amount"})
    death = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": refs["enemy_death"],
                                          "script": "res://scripts/enemy.gd", "function": "die"})
    assert hit["validation"]["level"] == "engine_parsed", hit["validation"]
    assert death["validation"]["level"] == "engine_parsed", death["validation"]

    code, log = _trigger(demo)
    assert code == 0 and "overall=PASS" in log, log

    for r in (death, hit):
        call("integrate_game_audio", {"project_path": str(demo), "action": "revert", "integration_id": r["integration_id"]})
    code, _ = _trigger(demo)
    assert code != 0


def test_background_music_end_to_end(call, demo, monkeypatch):
    monkeypatch.setenv("HLGA_GODOT_EXECUTABLE", GODOT)
    r = call("generate_music", {"project_path": str(demo), "genre": "chiptune", "description": "heroic overworld theme",
                                "bpm": 140, "key": "D minor", "duration_seconds": 8, "backend": "procedural"})
    assert r["success"] and r["embedded_loop"], r
    m = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": r["engine_ref"],
                                      "script": "res://scripts/main.gd", "function": "_ready", "mode": "music", "volume_db": -6})
    assert m["validation"]["level"] == "engine_parsed", m["validation"]
    out = subprocess.run([GODOT, "--headless", "--path", str(demo), "--script", "res://tests/music_test.gd"],
                         capture_output=True, text=True, timeout=120)
    log = out.stdout + out.stderr
    assert out.returncode == 0 and "overall=PASS" in log, log
    assert "loop_mode=1" in log  # Godot imported the embedded smpl loop points


def test_sfx_from_ready_during_scene_setup(call, demo, monkeypatch):
    """Regression: add_child() during scene setup used to fail ("Parent node is busy")."""
    monkeypatch.setenv("HLGA_GODOT_EXECUTABLE", GODOT)
    g = call("generate_sound", {"description": "spawn pop", "duration_seconds": 0.4, "backend": "procedural",
                                "project_path": str(demo), "filename": "spawn"})
    r = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": g["engine_ref"],
                                      "script": "res://scripts/enemy.gd", "function": "_ready"})
    assert r["validation"]["level"] == "engine_parsed", r["validation"]
    out = subprocess.run([GODOT, "--headless", "--path", str(demo), "--script", "res://tests/ready_test.gd"],
                         capture_output=True, text=True, timeout=120)
    log = out.stdout + out.stderr
    assert out.returncode == 0 and "overall=PASS" in log, log
    assert "busy setting up children" not in log
