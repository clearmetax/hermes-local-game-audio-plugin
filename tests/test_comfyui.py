import json
from pathlib import Path

import fake_comfyui
import pytest
from hlga_under_test import errors as E
from hlga_under_test.audio_backend import GenerationRequest
from hlga_under_test.audio_backend.comfyui import ComfyUIBackend, ace_keyscale, describe_workflow, patch_workflow

HERE = Path(__file__).parent
SAO = HERE.parent / "examples" / "comfyui" / "stable_audio_open_api.json"
ACE = HERE / "fixtures" / "ace_step_15_api.json"


@pytest.fixture
def comfy(monkeypatch):
    servers = []

    def start(**kw):
        f = fake_comfyui.FakeComfy(**kw)
        servers.append(f)
        monkeypatch.setenv("HLGA_COMFYUI_URL", f.url)
        monkeypatch.setenv("HLGA_COMFYUI_WORKFLOW_MUSIC", str(ACE))
        monkeypatch.setenv("HLGA_COMFYUI_WORKFLOW_SFX", str(SAO))
        return f

    yield start
    for s in servers:
        s.close()


def test_describe_workflows():
    sao = describe_workflow(json.loads(SAO.read_text()))
    ace = describe_workflow(json.loads(ACE.read_text()))
    assert sao["model_family"] == "stable_audio" and sao["prompt_node"] == "6" and sao["negative_node"] == "7"
    assert ace["model_family"] == "ace_step_1.5" and ace["supports"]["bpm"] and ace["negative_node"] is None


def test_rejects_ui_format_workflow(tmp_path):
    p = tmp_path / "ui.json"
    p.write_text(json.dumps({"nodes": [], "links": []}))
    with pytest.raises(E.AudioPluginError, match="API format"):
        ComfyUIBackend.load_workflow(str(p))


def test_patch_ace_step_fields(tmp_path):
    wf = json.loads(ACE.read_text())
    req = GenerationRequest("synthwave tags", 30.0, tmp_path / "x.wav", seed=5,
                            params={"bpm": 100, "key": "F# minor", "beats_per_bar": 3, "lyrics": None})
    out, patched = patch_workflow(wf, req, "hlga/abc")
    enc = out["2"]["inputs"]
    assert (enc["tags"], enc["bpm"], enc["keyscale"], enc["duration"], enc["seed"], enc["timesignature"], enc["lyrics"]) == \
        ("synthwave tags", 100, "F# minor", 30.0, 5, "3", "[instrumental]")
    assert out["4"]["inputs"]["seconds"] == 30.0 and out["5"]["inputs"]["seed"] == 5
    assert out["7"]["inputs"]["filename_prefix"] == "hlga/abc"
    assert wf["2"]["inputs"]["tags"] == "pop"  # original untouched


def test_patch_stable_audio_negative_and_steps(tmp_path):
    wf = json.loads(SAO.read_text())
    req = GenerationRequest("sword hit", 2.0, tmp_path / "x.wav", seed=1, params={"negative_prompt": "music", "steps": 80, "cfg": 6})
    out, _ = patch_workflow(wf, req, "hlga/x")
    assert out["6"]["inputs"]["text"] == "sword hit" and out["7"]["inputs"]["text"] == "music"
    assert out["10"]["inputs"]["seconds_total"] == 2.0 and out["11"]["inputs"]["seconds"] == 2.0
    assert out["3"]["inputs"]["steps"] == 80 and out["3"]["inputs"]["cfg"] == 6.0


@pytest.mark.parametrize("raw,expected", [("Dm", "D minor"), ("f# major", "F# major"), ("Bb", "Bb major"), ("A minor", "A minor"), ("xyz", None)])
def test_ace_keyscale(raw, expected):
    assert ace_keyscale(raw) == expected


def test_music_through_comfyui_end_to_end(call, comfy):
    f = comfy()
    r = call("generate_music", {"genre": "synthwave", "description": "night drive", "bpm": 100, "key": "F# minor",
                                "duration_seconds": 6, "seed": 42})
    assert r["success"], r
    assert r["backend"] == "comfyui" and r["backend_capabilities"]["bpm"] is True
    assert not any("cannot force a tempo" in n for n in r["notes"])
    enc = f.prompts[-1]["2"]["inputs"]
    assert enc["bpm"] == 100 and enc["keyscale"] == "F# minor" and enc["seed"] == 42
    assert Path(r["path"]).is_file() and r["embedded_loop"]


def test_sfx_routes_to_sfx_workflow_with_negative_prompt(call, comfy):
    f = comfy()
    r = call("generate_sound", {"description": "sword hit", "duration_seconds": 1, "candidates": 2, "seed": 7})
    assert r["success"], r
    assert [p["3"]["inputs"]["seed"] for p in f.prompts] == [7, 8]
    assert "music" in f.prompts[-1]["7"]["inputs"]["text"]  # default SFX negative prompt
    assert f.prompts[-1]["6"]["inputs"]["text"].startswith("sword hit")


def test_comfyui_rejected_workflow(call, comfy):
    comfy(fail="reject")
    r = call("generate_sound", {"description": "x", "backend": "comfyui"})
    assert r["error"]["code"] == E.BACKEND_ERROR and "ckpt_name" in json.dumps(r["error"])


def test_comfyui_execution_error(call, comfy):
    comfy(fail="exec")
    r = call("generate_sound", {"description": "x", "backend": "comfyui"})
    assert r["error"]["code"] == E.BACKEND_ERROR and "out of memory" in r["error"]["message"]


def test_comfyui_timeout_interrupts(call, comfy, monkeypatch):
    f = comfy(slow=True)
    monkeypatch.setenv("HLGA_GENERATION_TIMEOUT_SECONDS", "10")
    import hlga_under_test.audio_backend.comfyui as cm

    monkeypatch.setattr(cm.time, "monotonic", _fast_clock())
    r = call("generate_sound", {"description": "x", "backend": "comfyui"})
    assert r["error"]["code"] == E.GENERATION_TIMEOUT and f.interrupted == 1


def _fast_clock():
    t = [0.0]

    def clock():
        t[0] += 3.0
        return t[0]

    return clock


def test_comfyui_unreachable(call, monkeypatch):
    monkeypatch.setenv("HLGA_COMFYUI_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HLGA_COMFYUI_WORKFLOW_SFX", str(SAO))
    r = call("generate_sound", {"description": "x", "backend": "comfyui"})
    assert r["error"]["code"] == E.BACKEND_NOT_READY


def test_remote_comfyui_refused(call, monkeypatch):
    monkeypatch.setenv("HLGA_COMFYUI_URL", "http://example.com:8188")
    monkeypatch.setenv("HLGA_COMFYUI_WORKFLOW_SFX", str(SAO))
    r = call("generate_sound", {"description": "x", "backend": "comfyui"})
    assert r["error"]["code"] == E.PATH_REJECTED


def test_status_deep_checks_nodes(call, comfy):
    comfy()
    st = call("audio_status", {"deep_check": True})
    c = st["backends"]["comfyui"]
    assert c["ready"] is True and c["details"]["workflows"]["music"]["model_family"] == "ace_step_1.5"
    assert st["routing"] == {"sfx": "comfyui", "music": "comfyui", "setting_backend": "auto", "setting_music_backend": "auto"}


def test_status_reports_missing_custom_nodes(call, comfy, tmp_path, monkeypatch):
    comfy()
    wf = json.loads(SAO.read_text())
    wf["99"] = {"class_type": "SomeCustomNodeNotInstalled", "inputs": {}}
    p = tmp_path / "wf.json"
    p.write_text(json.dumps(wf))
    monkeypatch.setenv("HLGA_COMFYUI_WORKFLOW_SFX", str(p))
    st = call("audio_status", {"deep_check": True})
    assert st["backends"]["comfyui"]["ready"] is False
    assert "SomeCustomNodeNotInstalled" in " ".join(st["backends"]["comfyui"]["problems"])


def test_music_backend_setting_splits_routing(call, comfy, monkeypatch):
    comfy()
    monkeypatch.delenv("HLGA_COMFYUI_WORKFLOW_SFX")
    monkeypatch.setenv("HLGA_MUSIC_BACKEND", "comfyui")
    monkeypatch.setenv("HLGA_BACKEND", "procedural")
    st = call("audio_status")
    assert st["routing"]["sfx"] == "procedural" and st["routing"]["music"] == "comfyui"
