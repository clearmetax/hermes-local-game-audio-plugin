import os

import pytest
from hlga_under_test import errors as E
from hlga_under_test import safety


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "proj"
    (r / "audio").mkdir(parents=True)
    return r.resolve()


@pytest.mark.parametrize("bad", ["../x.wav", "audio/../../x.wav", "res://../x.wav", "..\\x.wav", "C:\\Windows\\x.wav", "/etc/passwd"])
def test_traversal_and_absolute_rejected(root, bad):
    with pytest.raises(E.AudioPluginError) as ei:
        safety.resolve_inside(root, bad)
    assert ei.value.code == E.PATH_REJECTED


def test_res_and_relative_paths(root):
    assert safety.resolve_inside(root, "res://audio/a.wav") == root / "audio" / "a.wav"
    assert safety.resolve_inside(root, "audio\\b.wav") == root / "audio" / "b.wav"
    assert safety.resolve_inside(root, str(root / "audio" / "c.wav")) == root / "audio" / "c.wav"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlink_escape_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(E.AudioPluginError) as ei:
        safety.resolve_inside(root, "link/x.wav")
    assert ei.value.code == E.PATH_REJECTED
    with pytest.raises(E.AudioPluginError):
        safety.make_dirs_inside(root, root / "link" / "sub")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_symlinked_project_root_rejected(tmp_path, root):
    link = tmp_path / "rootlink"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(E.AudioPluginError):
        safety.resolve_project_root(str(link))


def test_allowed_roots(root, tmp_path):
    assert safety.resolve_project_root(str(root), [str(tmp_path)]) == root
    with pytest.raises(E.AudioPluginError) as ei:
        safety.resolve_project_root(str(root), [str(tmp_path / "elsewhere")])
    assert ei.value.code == E.PATH_REJECTED


def test_missing_project(tmp_path):
    with pytest.raises(E.AudioPluginError) as ei:
        safety.resolve_project_root(str(tmp_path / "nope"))
    assert ei.value.code == E.PROJECT_NOT_FOUND


def test_publish_never_overwrites(root):
    dest = root / "audio" / "a.wav"
    safety.publish_exclusive(b"one", dest)
    with pytest.raises(E.AudioPluginError) as ei:
        safety.publish_exclusive(b"two", dest)
    assert ei.value.code == E.FILE_EXISTS
    assert dest.read_bytes() == b"one"
    assert not [p for p in (root / "audio").iterdir() if p.name.startswith(".hlga-")]


def test_publish_out_of_space(root, monkeypatch):
    monkeypatch.setattr(safety, "MIN_FREE_BYTES", 1 << 62)
    with pytest.raises(E.AudioPluginError) as ei:
        safety.publish_exclusive(b"x", root / "audio" / "z.wav")
    assert ei.value.code == E.OUT_OF_SPACE


def test_unique_path(root):
    (root / "audio" / "hit.wav").write_bytes(b"x")
    assert safety.unique_path(root / "audio", "hit").name == "hit_02.wav"


@pytest.mark.parametrize("raw,expected", [("Sword Hit!!", "sword_hit"), ("../../evil", "evil"), ("CON", "sfx"), ("x.wav", "x"), ("", "sfx"), ("Ünïcödé boom", "unicode_boom")])
def test_safe_stem(raw, expected):
    assert safety.safe_stem(raw) == expected


def test_atomic_replace_detects_concurrent_change(root):
    f = root / "a.gd"
    f.write_text("one")
    with pytest.raises(E.AudioPluginError) as ei:
        safety.atomic_replace_text(f, "different", "two")
    assert ei.value.code == E.CONFLICT
    assert f.read_text() == "one"


def test_child_env_drops_hermes_state_and_secrets():
    env = safety.child_env({
        "PATH": "/usr/bin", "SYSTEMROOT": "C:\\Windows", "AGENT_AUDIO_HOME": "/data",
        "HERMES_HOME": "/h", "HERMES_YOLO_MODE": "1", "OPENAI_API_KEY": "sk-x", "HF_TOKEN": "hf_x",
        "GITHUB_TOKEN": "g", "AWS_SECRET_ACCESS_KEY": "a", "DB_PASSWORD": "p",
    })
    assert env == {"PATH": "/usr/bin", "SYSTEMROOT": "C:\\Windows", "AGENT_AUDIO_HOME": "/data"}
