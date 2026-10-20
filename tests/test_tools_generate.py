from pathlib import Path

import pytest
from hlga_under_test import errors as E
from hlga_under_test import safety, tools, wav


def test_status_without_backend(call):
    r = call("audio_status", {"deep_check": True})
    assert r["success"] and r["ai_backend"]["configured"] is False
    assert r["ai_backend"]["setup_steps"] and r["procedural_backend"]["ready"]
    assert "ram_gb" in r["machine"]


def test_generate_without_backend_is_not_configured(call, tmp_path):
    r = call("generate_sound", {"description": "sword hit"})
    assert r["success"] is False and r["error"]["code"] == E.BACKEND_NOT_CONFIGURED
    # nothing written to the library
    lib = Path(call("audio_status")["paths"]["library_dir"])
    assert not lib.exists() or not list(lib.glob("*.wav"))


def test_auto_never_falls_back_to_procedural(call):
    r = call("generate_sound", {"description": "laser"})
    assert r["error"]["code"] == E.BACKEND_NOT_CONFIGURED


def test_generate_procedural_to_library(call):
    r = call("generate_sound", {"description": "retro laser zap", "duration_seconds": 0.5, "backend": "procedural"})
    assert r["success"], r
    assert r["is_ai_model"] is False and any("procedural" in n for n in r["notes"])
    p = Path(r["path"])
    info = wav.validate(p.read_bytes())
    assert p.name == "retro_laser_zap.wav" and info.bits_per_sample == 16 and p.parent.name == "sfx"
    assert r["quality"]["verdict"] in ("good", "acceptable") and r["mastering"]
    assert Path(r["manifest"]).is_file()


def test_generate_via_fake_agent_audio_into_project(call, fake_backend, demo):
    fake_backend("ok")
    r = call("generate_sound", {"description": "heavy sword hit", "duration_seconds": 0.5, "project_path": str(demo), "filename": "Sword Hit"})
    assert r["success"], r
    assert r["engine"] == "godot" and r["engine_ref"] == "res://audio/sfx/sword_hit.wav"
    assert r["backend"] == "agent_audio" and r["is_ai_model"] is True
    assert r["audio"]["encoding"] == "pcm" and r["audio"]["bits_per_sample"] == 16
    assert abs(r["quality"]["metrics"]["true_peak_dbtp"] - (-1.0)) < 0.3  # sfx profile: peak -1 dBTP
    assert "isolated game sound effect" in r["prompt_used"] and "no music" in r["prompt_used"]
    # renders are cleaned up
    renders = Path(call("audio_status")["paths"]["data_dir"]) / "renders"
    assert not list(renders.glob("*.wav"))


def test_conflict_rename_and_error(call, demo):
    args = {"description": "coin pickup", "duration_seconds": 0.3, "project_path": str(demo), "filename": "coin", "backend": "procedural"}
    a = call("generate_sound", args)
    b = call("generate_sound", args)
    assert a["project_path"] == "audio/sfx/coin.wav"
    assert b["project_path"] == "audio/sfx/coin_02.wav" and b["renamed_from"] == "coin.wav"
    original = (demo / "audio/sfx/coin.wav").read_bytes()
    c = call("generate_sound", {**args, "on_conflict": "error"})
    assert c["error"]["code"] == E.FILE_EXISTS
    assert (demo / "audio/sfx/coin.wav").read_bytes() == original


@pytest.mark.parametrize(
    "args",
    [
        {"description": ""},
        {"description": "x", "duration_seconds": 0},
        {"description": "x", "duration_seconds": 999},
        {"description": "x" * 2000},
        {"description": "x", "seed": "abc", "backend": "procedural"},
        {"description": "x", "backend": "cloud"},
        {"description": "x", "post_process": "loud"},
    ],
)
def test_input_validation(call, args):
    r = call("generate_sound", args)
    assert r["success"] is False and r["error"]["code"] == E.INVALID_INPUT


def test_subdir_traversal_rejected(call, demo):
    r = call("generate_sound", {"description": "x", "project_path": str(demo), "subdir": "../outside", "backend": "procedural"})
    assert r["error"]["code"] == E.PATH_REJECTED
    assert not (demo.parent / "outside").exists()


def test_allowed_roots_enforced(call, demo, monkeypatch, tmp_path):
    monkeypatch.setenv("HLGA_ALLOWED_PROJECT_ROOTS", str(tmp_path / "only-here"))
    r = call("generate_sound", {"description": "x", "project_path": str(demo), "backend": "procedural"})
    assert r["error"]["code"] == E.PATH_REJECTED


def test_invalid_backend_wav_never_published(call, fake_backend, demo):
    fake_backend("badwav")
    r = call("generate_sound", {"description": "boom", "duration_seconds": 0.5, "project_path": str(demo)})
    assert r["error"]["code"] == E.INVALID_OUTPUT
    assert not (demo / "audio").exists() or not list((demo / "audio").rglob("*.wav"))


def test_out_of_space(call, demo, monkeypatch):
    monkeypatch.setattr(safety, "MIN_FREE_BYTES", 1 << 62)
    r = call("generate_sound", {"description": "boom", "project_path": str(demo), "backend": "procedural"})
    assert r["error"]["code"] == E.OUT_OF_SPACE


def test_variations(call, demo):
    r = call("generate_variations", {"description": "footstep on stone", "count": 3, "duration_seconds": 0.3, "project_path": str(demo), "filename": "step", "backend": "procedural"})
    assert r["success"] and r["generated"] == 3 and not r["partial"]
    assert [t["project_path"] for t in r["takes"]] == ["audio/sfx/step_v01.wav", "audio/sfx/step_v02.wav", "audio/sfx/step_v03.wav"]
    assert len({t["prompt_used"] for t in r["takes"]}) == 3


def test_variations_partial_failure(call, fake_backend, demo):
    fake_backend("crash2")  # second generation crashes the server
    r = call("generate_variations", {"description": "hit", "count": 3, "duration_seconds": 0.3, "project_path": str(demo)})
    assert r["success"] and r["partial"] and r["generated"] >= 1 and r["failures"]


def test_variations_bounded(call):
    r = call("generate_variations", {"description": "hit", "count": 50, "backend": "procedural"})
    assert r["error"]["code"] == E.INVALID_INPUT


def test_build_prompt_respects_music_and_voice():
    p = tools.build_sfx_prompt("victory jingle with a short melody", "retro 8-bit")
    assert "no music" not in p and "retro 8-bit style" in p and "no voice" in p
    assert "no voice" not in tools.build_sfx_prompt("orc grunt of pain")
