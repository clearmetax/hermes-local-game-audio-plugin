"""Regression tests for issues found in independent review."""

import hashlib
import json
import math
import random
from array import array
from pathlib import Path

import pytest
from hlga_under_test import dsp, mastering, wav
from hlga_under_test import errors as E
from hlga_under_test.audio_backend import GenerationRequest
from hlga_under_test.audio_backend.comfyui import describe_workflow, patch_workflow
from hlga_under_test.game_adapters import GodotAdapter


def test_loudness_consistent_across_20s_boundary():
    rnd = random.Random(1)
    rate = 48000

    def noise(secs):
        return dsp.Audio([array("d", (rnd.uniform(-0.3, 0.3) for _ in range(int(secs * rate))))], rate)

    a, b = dsp.integrated_loudness(noise(19.9)), dsp.integrated_loudness(noise(20.1))
    assert abs(a - b) < 0.2


def test_bright_tone_loudness_long_file():
    rate = 48000
    t = array("d", (0.3 * math.sin(2 * math.pi * 12000 * i / rate) for i in range(int(25 * rate))))
    short = dsp.integrated_loudness(dsp.Audio([t[: 10 * rate]], rate))
    long_ = dsp.integrated_loudness(dsp.Audio([t], rate))
    assert abs(short - long_) < 0.2


def test_loop_shorter_than_crossfade_is_rejected_cleanly(call, tmp_path):
    rate = 44100
    f = tmp_path / "a.wav"
    f.write_bytes(wav.encode(dsp.Audio([array("d", (0.3 * math.sin(i * 0.03) for i in range(5 * rate)))], rate)))
    r = call("process_audio", {"path": str(f), "loop": True, "loop_seconds": 0.3, "crossfade_seconds": 1.0})
    assert r["success"] is False and r["error"]["code"] == E.INVALID_INPUT
    with pytest.raises(ValueError):
        dsp.make_loop(dsp.Audio([array("d", (0.3 * math.sin(i * 0.03) for i in range(5 * rate)))], rate), loop_seconds=0.3, crossfade_s=1.0)


def test_music_crossfade_too_long_rejected_before_generating(call):
    r = call("generate_music", {"genre": "x", "duration_seconds": 2, "bpm": 200, "crossfade_seconds": 3, "backend": "procedural"})
    assert r["error"]["code"] == E.INVALID_INPUT and "crossfade" in r["error"]["message"]


def test_sub_bar_loop_not_reported_as_bar_aligned():
    rate = 44100
    a = dsp.Audio([array("d", (0.3 * math.sin(i * 0.03) for i in range(3 * rate)))], rate)
    _, info = dsp.make_loop(a, crossfade_s=0.2, bpm=60)
    assert info["bars"] is None and info["bpm_grid"] is None and info["seam"]["method"].startswith("phase")


def test_gdscript_hash_inside_string():
    src = 'extends Node\n\nfunc set_color(c: String = "#ff0000") -> void:\n\tvar ok := true\n\tif ok:\n\t\tprint(c)\n'
    out = GodotAdapter(Path(".")).plan_script_edit(src, function="set_color", asset_ref="res://a.wav")["text"]
    lines = out.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith("func set_color"))
    assert lines[i + 1].startswith("\t_hlga_play_sfx(")


def test_bom_script_supported(call, demo):
    g = call("generate_sound", {"description": "hit", "backend": "procedural", "project_path": str(demo), "filename": "hit"})
    f = demo / "scripts" / "bom.gd"
    f.write_bytes("﻿extends Node2D\n\nfunc hit():\n\tpass\n".encode("utf-8"))
    r = call("integrate_game_audio", {"project_path": str(demo), "action": "apply", "sound": g["engine_ref"],
                                      "script": "res://scripts/bom.gd", "function": "hit", "run_engine_checks": False})
    assert r["applied"], r
    assert f.read_bytes().startswith("﻿extends".encode("utf-8"))


def test_variations_with_fixed_seed_differ(call):
    r = call("generate_variations", {"description": "hit", "count": 3, "seed": 5, "vary_prompt": False,
                                     "duration_seconds": 0.3, "backend": "procedural"})
    hashes = {hashlib.md5(Path(t["path"]).read_bytes()).hexdigest() for t in r["takes"]}
    assert len(hashes) == 3


def test_ffmpeg_size_patch_walks_chunks(monkeypatch, tmp_path):
    from hlga_under_test import codecs_io

    payload = b"\x00\x01" * 100
    fmt = b"fmt " + (16).to_bytes(4, "little") + b"\x01\x00\x01\x00" + (44100).to_bytes(4, "little") + (88200).to_bytes(4, "little") + b"\x02\x00\x10\x00"
    info = b"LIST" + (12).to_bytes(4, "little") + b"INFOdata\x00\x00\x00\x00"
    raw = b"RIFF\xff\xff\xff\xffWAVE" + fmt + info + b"data\xff\xff\xff\xff" + payload

    class P:
        returncode, stdout, stderr = 0, raw, b""

    monkeypatch.setattr(codecs_io.subprocess, "run", lambda *a, **k: P())
    out = codecs_io._ffmpeg_to_wav("ffmpeg", tmp_path / "x.flac")
    assert wav.inspect_bytes(out).frames == 100


def test_all_sampler_prompts_patched(tmp_path):
    wf = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "OLD A"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 0, "positive": ["1", 0], "negative": ["2", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "OLD B"}},
        "5": {"class_type": "KSampler", "inputs": {"seed": 0, "positive": ["4", 0], "negative": ["2", 0]}},
        "6": {"class_type": "SaveAudio", "inputs": {"filename_prefix": "x"}},
    }
    out, _ = patch_workflow(wf, GenerationRequest("NEW", 1.0, tmp_path / "x.wav", seed=1, params={"negative_prompt": "N"}), "p")
    assert out["1"]["inputs"]["text"] == out["4"]["inputs"]["text"] == "NEW" and out["2"]["inputs"]["text"] == "N"
    assert describe_workflow(wf)["prompt_nodes"] == ["1", "4"]


def test_limiter_reporting_is_truthful():
    rnd = random.Random(4)
    x = array("d", (rnd.uniform(-0.002, 0.002) for _ in range(44100 * 6)))
    for k in range(0, len(x), 22050):
        x[k] = 0.9
    out, steps, _ = mastering.master(dsp.Audio([x], 44100), mastering.MasterOptions(kind="music"))
    assert any(s.startswith("limited") for s in steps)
    fin = next(s for s in steps if s.startswith("final loudness"))
    final = float(fin.split("final loudness ")[1].split(" LUFS")[0])
    assert abs(final - dsp.integrated_loudness(out)) < 0.2


def test_validate_rejects_far_too_short():
    w = wav.encode(dsp.Audio([array("d", (0.3 * math.sin(i * 0.1) for i in range(2646)))], 44100))  # 0.06 s
    with pytest.raises(E.AudioPluginError):
        wav.validate(w, expected_seconds=30)


def test_normalize_peak_none_disables_leveling():
    a = dsp.Audio([array("d", (0.01 * math.sin(i * 0.05) for i in range(44100 * 3)))], 44100)
    before = dsp.integrated_loudness(a)
    out, steps, _ = mastering.master(a, mastering.MasterOptions.from_args("music", {"normalize_peak_db": None}))
    assert abs(dsp.integrated_loudness(out) - before) < 0.6 and not any("loudness" in s for s in steps)


def test_prepare_rejects_short_stinger_and_name_collisions(call, demo):
    r = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": [
        {"name": "win", "kind": "stinger", "description": "x", "duration_seconds": 0.5}]})
    assert r["error"]["code"] == E.INVALID_INPUT
    r = call("prepare_game_audio", {"project_path": str(demo), "feature": "f", "sounds": [
        {"name": "hit", "description": "x", "variations": 2}, {"name": "hit_v01", "description": "y"}]})
    assert r["error"]["code"] == E.INVALID_INPUT and "collides" in r["error"]["message"]


def test_loop_guard_frame_matches_start():
    a = dsp.Audio([array("d", (0.3 * math.sin(i * 0.05) for i in range(1000)))], 44100)
    raw = wav.encode(a, loop=True, dither=False)
    d = wav.decode(raw)
    assert d.frames == 1001 and d.channels[0][-1] == d.channels[0][0]
    assert wav.read_loop(raw) == (0, 1000)
    json.dumps(wav.inspect_bytes(raw).to_dict())


def test_loop_built_even_with_mastering_disabled(call):
    r = call("generate_music", {"genre": "chiptune", "description": "loop", "bpm": 120, "duration_seconds": 6,
                                "backend": "procedural", "post_process": {"enabled": False}})
    assert r["success"] and r["embedded_loop"] and r["loop"], r
    assert "levels untouched" in r["mastering"][0] and "Seamless loop" in r["note"]


def test_process_audio_reports_ogg_fallback(call, tmp_path, monkeypatch):
    from hlga_under_test import tools

    monkeypatch.setattr(tools, "find_ffmpeg", lambda configured="": None)
    f = tmp_path / "a.wav"
    f.write_bytes(wav.encode(dsp.Audio([array("d", (0.3 * math.sin(i * 0.03) for i in range(44100)))], 44100)))
    r = call("process_audio", {"path": str(f), "kind": "sfx", "output_format": "ogg"})
    assert r["path"].endswith(".wav") and "ffmpeg not found" in r["format_note"]


def test_normalize_false_keeps_level():
    a = dsp.Audio([array("d", (0.01 * math.sin(i * 0.05) for i in range(44100 * 3)))], 44100)
    before = dsp.integrated_loudness(a)
    out, steps, _ = mastering.master(a, mastering.MasterOptions.from_args("music", {"normalize": False}))
    assert abs(dsp.integrated_loudness(out) - before) < 0.6
