import json
import os
import shutil
from pathlib import Path

import pytest
from hlga_under_test import errors as E
from hlga_under_test import pipeline, tools, wav


def _music(call, demo=None, **kw):
    args = {"genre": "chiptune", "description": "heroic overworld theme", "bpm": 128, "key": "D minor",
            "duration_seconds": 8, "backend": "procedural", **kw}
    if demo is not None:
        args["project_path"] = str(demo)
    return call("generate_music", args)


def test_music_loop_is_bar_exact_mastered_and_has_loop_points(call, demo):
    r = _music(call, demo)
    assert r["success"], r
    assert r["engine_ref"].startswith("res://audio/music/") and r["kind"] == "music"
    raw = Path(r["path"]).read_bytes()
    a = wav.decode(raw)
    bar = 4 * 60 / 128
    assert abs(a.seconds / bar - round(a.seconds / bar)) < 0.01  # whole bars
    assert wav.read_loop(raw) == (0, a.frames - 1) and r["embedded_loop"]
    m = r["quality"]["metrics"]
    assert abs(m["integrated_lufs"] - (-16.0)) < 0.7 and m["true_peak_dbtp"] <= -0.85
    assert r["loop"]["bars"] and r["loop"]["seam"]["score"] >= 60
    assert "128 BPM" in r["prompt_used"] and "seamless loop" in r["prompt_used"] and "no vocals" in r["prompt_used"]


def test_stinger_is_not_looped_and_ends_cleanly(call, demo):
    r = _music(call, demo, kind="stinger", description="victory fanfare", duration_seconds=3)
    assert r["success"] and r["loop"] is None and not r.get("embedded_loop")
    assert "abrupt_end" not in {i["code"] for i in r["quality"]["issues"]}
    assert "clear decisive ending" in r["prompt_used"]


def test_one_off_track_without_loop(call):
    r = _music(call, loop=False, duration_seconds=6)
    assert r["success"] and r["loop"] is None and Path(r["path"]).parent.name == "music"


def test_music_validation(call):
    assert _music(call, bpm=500)["error"]["code"] == E.INVALID_INPUT
    assert _music(call, key="H minor")["error"]["code"] == E.INVALID_INPUT
    assert call("generate_music", {"backend": "procedural"})["error"]["code"] == E.INVALID_INPUT
    assert _music(call, duration_seconds=10_000)["error"]["code"] == E.INVALID_INPUT


def test_capability_notes_are_honest(call, fake_backend):
    fake_backend("ok")
    r = call("generate_music", {"description": "calm piano", "bpm": 90, "key": "C major", "duration_seconds": 6, "loop": False})
    assert r["success"], r
    joined = " ".join(r["notes"])
    assert "cannot force a tempo" in joined and "requested in the prompt only" in joined
    assert r["backend_capabilities"]["bpm"] is False


def test_lyrics_ignored_note_for_backends_without_lyrics(call):
    r = _music(call, lyrics="[verse] hello", duration_seconds=5, loop=False)
    assert any("lyrics" in n for n in r["notes"])


def test_best_of_candidates_and_alternates(call, demo):
    r = call("generate_sound", {"description": "retro laser zap", "duration_seconds": 0.5, "backend": "procedural",
                                "project_path": str(demo), "candidates": 3, "keep_alternates": True})
    c = r["candidates"]
    assert c["generated"] == 3 and len(c["alternates"]) == 2
    totals = [s["total"] for s in c["scores"]]
    assert totals == sorted(totals, reverse=True) and c["chosen_take"] == c["scores"][0]["take"]
    assert all(Path(a["path"]).is_file() for a in c["alternates"])
    assert all(Path(a["file"]).is_file() for a in c["alternates"])


def test_min_score_gate_keeps_trying(call, monkeypatch):
    seen = []
    real = pipeline.Generator._render

    def fake_render(self, spec, attempt, workdir):
        t = real(self, spec, attempt, workdir)
        seen.append(attempt)
        t["pre"]["rank_score"] = t["post"]["rank_score"] = 50.0 if attempt < 2 else 99.0
        return t

    monkeypatch.setattr(pipeline.Generator, "_render", fake_render)
    r = call("generate_sound", {"description": "coin", "duration_seconds": 0.3, "backend": "procedural",
                                "candidates": 1, "min_score": 90, "max_attempts": 4})
    assert seen == [0, 1, 2] and r["quality"]["ranking_score"] >= 90


def test_semantic_scores_change_the_ranking(call, monkeypatch):
    from hlga_under_test import semantic

    monkeypatch.setattr(semantic, "find_python", lambda configured="": "/fake/python")

    def fake_score(files, prompt, kind, *, python, model, timeout=600):
        return {"available": True, "model": "fake", "scores": [{"cosine": 0.05 if i == 0 else 0.45, "prompt_probability": 0.5} for i in range(len(files))]}

    monkeypatch.setattr(semantic, "score", fake_score)
    r = call("generate_sound", {"description": "coin", "duration_seconds": 0.3, "backend": "procedural", "candidates": 2, "seed": 1})
    assert r["candidates"]["chosen_take"] == 2
    assert r["quality"]["semantic"]["points"] == 100.0


def test_folder_routing_sfx_vs_music_vs_ambience(call, demo):
    m = _music(call, demo)
    s = call("generate_sound", {"description": "laser", "backend": "procedural", "project_path": str(demo)})
    a = call("generate_sound", {"description": "night forest wind", "kind": "ambience", "duration_seconds": 6,
                                "backend": "procedural", "project_path": str(demo)})
    assert m["project_path"].startswith("audio/music/")
    assert s["project_path"].startswith("audio/sfx/")
    assert a["project_path"].startswith("audio/ambience/") and a["embedded_loop"]
    assert abs(a["quality"]["metrics"]["integrated_lufs"] - (-24)) < 0.8


def test_unity_music_folder_convention(call, tmp_path):
    root = tmp_path / "u"
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "Assets").mkdir()
    (root / "ProjectSettings/ProjectVersion.txt").write_text("m_EditorVersion: 2022.3.1f1\n")
    r = _music(call, root, filename="Boss Theme")
    assert r["project_path"] == "Assets/Audio/Music/BossTheme.wav" or r["project_path"] == "Assets/Audio/Music/boss_theme.wav"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_ogg_export(call, demo):
    r = _music(call, demo, output_format="ogg")
    assert r["format"] == "ogg" and r["path"].endswith(".ogg") and Path(r["path"]).read_bytes()[:4] == b"OggS"


def test_ogg_falls_back_to_wav_without_ffmpeg(call, demo, monkeypatch):
    monkeypatch.setattr(tools, "find_ffmpeg", lambda configured="": None)
    r = _music(call, demo, output_format="ogg")
    assert r["format"] == "wav" and "ffmpeg not found" in r["format_note"]


# --------------------------------------------------------------- analyze / process
def test_analyze_reports_loop_and_metrics(call, demo):
    r = _music(call, demo)
    a = call("analyze_audio", {"project_path": str(demo), "path": r["engine_ref"], "bpm": 128})
    assert a["success"] and a["embedded_loop"] and a["kind"] == "music"
    assert "integrated_lufs" in a["metrics"] and "loop_seam" in a["metrics"]
    assert a["metrics"]["tempo_matches_request"] is True


def test_analyze_requires_safe_paths(call, demo, tmp_path):
    assert call("analyze_audio", {"path": "relative.wav"})["error"]["code"] == E.INVALID_INPUT
    assert call("analyze_audio", {"project_path": str(demo), "path": "../x.wav"})["error"]["code"] == E.PATH_REJECTED


def test_process_audio_loops_and_never_overwrites(call, demo):
    r = call("generate_sound", {"description": "wind", "kind": "ambience", "loop": False, "duration_seconds": 8,
                                "backend": "procedural", "project_path": str(demo), "filename": "wind"})
    src = Path(r["path"])
    before = src.read_bytes()
    p = call("process_audio", {"project_path": str(demo), "path": r["project_path"], "kind": "ambience", "loop": True})
    assert p["success"], p
    assert src.read_bytes() == before and p["path"] != str(src) and p["path"].endswith("_loop.wav")
    assert wav.read_loop(Path(p["path"]).read_bytes())
    p2 = call("process_audio", {"project_path": str(demo), "path": r["project_path"], "kind": "ambience", "loop": True})
    assert p2["path"].endswith("_loop_02.wav")


def test_process_audio_fixes_late_start_and_level(call, tmp_path):
    import math
    from array import array

    from hlga_under_test import dsp

    x = array("d", [0.0]) * 9000 + array("d", (0.05 * math.sin(i * 0.05) * math.exp(-i / 9000) for i in range(30000)))
    f = tmp_path / "quiet.wav"
    f.write_bytes(wav.encode(dsp.Audio([x], 44100)))
    p = call("process_audio", {"path": str(f), "kind": "sfx"})
    assert "late_start" in p["before"]["issues"] and "late_start" not in p["after"]["issues"]
    assert abs(p["after"]["metrics"]["true_peak_dbtp"] - (-1.0)) < 0.3


# --------------------------------------------------------------- game workflow
def test_prepare_mixed_feature(call, demo):
    r = call("prepare_game_audio", {"project_path": str(demo), "feature": "boss fight", "backend": "procedural", "sounds": [
        {"name": "boss_theme", "kind": "music", "description": "intense battle", "bpm": 150, "duration_seconds": 6},
        {"name": "boss_roar", "description": "monster roar", "duration_seconds": 1},
        {"name": "victory", "kind": "stinger", "description": "victory fanfare", "duration_seconds": 3},
    ]})
    assert r["success"], r
    kinds = {a["name"]: (a["kind"], a["path"], a["loop"]) for a in r["assets"]}
    assert kinds["boss_theme"][1].startswith("audio/music/") and kinds["boss_theme"][2] is True
    assert kinds["boss_roar"][1].startswith("audio/sfx/")
    assert kinds["victory"][0] == "stinger" and kinds["victory"][2] is False


def test_integrate_music_mode(call, demo):
    r = _music(call, demo)
    m = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": r["engine_ref"],
                                      "script": "res://scripts/main.gd", "function": "_ready", "mode": "music",
                                      "volume_db": -6, "run_engine_checks": False})
    assert m["applied"], m
    text = (demo / "scripts/main.gd").read_text()
    assert "const MUSIC_" in text and "_hlga_play_music(MUSIC_" in text and "LOOP_FORWARD" in text
    assert text.count("func _hlga_play_music(") == 1
    again = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": r["engine_ref"],
                                          "script": "res://scripts/main.gd", "function": "_ready", "mode": "music",
                                          "volume_db": -6, "run_engine_checks": False})
    assert again["status"] == "already_integrated"


def test_status_reports_routing_and_quality_tools(call):
    st = call("audio_status")
    assert st["routing"] == {"sfx": "agent_audio", "music": "agent_audio", "setting_backend": "auto", "setting_music_backend": "auto"}
    assert "clap_prompt_match" in st["quality_tools"] and set(st["backends"]) == {"agent_audio", "comfyui", "procedural"}


@pytest.mark.skipif(not os.environ.get("HLGA_TEST_CLAP_PYTHON"), reason="set HLGA_TEST_CLAP_PYTHON to a Python with torch+transformers")
def test_real_clap_scorer_prefers_matching_audio(tmp_path, monkeypatch):
    from hlga_under_test import semantic
    from hlga_under_test.audio_backend import GenerationRequest, ProceduralBackend

    files = []
    with ProceduralBackend().session() as s:
        for name, prompt, kind, secs in [("coin", "coin pickup", "sfx", 0.5), ("rain", "rain falling", "ambience", 6)]:
            out = tmp_path / f"{name}.wav"
            s.generate(GenerationRequest(prompt, secs, out, seed=1, params={"kind": kind, "bpm": 120}))
            files.append(out)
    py = os.environ["HLGA_TEST_CLAP_PYTHON"]
    r1 = semantic.score(files, "a bright coin pickup chime", "sfx", python=py)
    r2 = semantic.score(files, "rain falling ambience", "ambience", python=py)
    assert r1["available"] and r2["available"], (r1, r2)
    assert r1["scores"][0]["cosine"] > r1["scores"][1]["cosine"]
    assert r2["scores"][1]["cosine"] > r2["scores"][0]["cosine"]
    print(json.dumps({"coin_prompt": r1["scores"], "rain_prompt": r2["scores"]}))
