import re
import subprocess
import sys

import _load
from hlga_under_test import schemas, tools


class FakeCtx:
    def __init__(self):
        self.tools, self.skills, self.hooks, self.commands = {}, {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert name not in self.tools
        self.tools[name] = (toolset, schema, handler)

    def register_skill(self, name, path):
        self.skills[name] = path

    def register_hook(self, name, cb):
        self.hooks[name] = cb

    def register_command(self, name, handler, description=""):
        self.commands[name] = handler

    def get_config(self, key, default=None):
        return default


def _manifest_list(field):
    text = (_load.PLUGIN_DIR / "plugin.yaml").read_text()
    block = re.search(rf"^{field}:\n((?:  - .+\n)+)", text, re.M).group(1)
    return [ln.strip()[2:] for ln in block.splitlines()]


def test_declared_tools_match_registered_and_schemas():
    ctx = FakeCtx()
    _load.load().register(ctx)
    declared = _manifest_list("provides_tools")
    assert sorted(declared) == sorted(ctx.tools) == sorted(s["name"] for s in schemas.ALL)
    assert set(tools.HANDLERS) == set(declared)
    assert sorted(_manifest_list("provides_hooks")) == sorted(ctx.hooks)
    assert all(t[0] == "game_audio" for t in ctx.tools.values())


def test_skill_and_command_registered():
    ctx = FakeCtx()
    _load.load().register(ctx)
    assert "game-audio" in ctx.skills and ctx.skills["game-audio"].is_file()
    out = ctx.commands["game-audio"]("")
    assert "Hermes Local Game Audio" in out


def test_handlers_return_json_strings_even_on_bad_input():
    ctx = FakeCtx()
    _load.load().register(ctx)
    for name, (_, _, handler) in ctx.tools.items():
        out = handler({"project_path": 12345, "description": None}, task_id="t1")
        assert isinstance(out, str) and out.startswith("{"), name
        out2 = handler("not-a-dict")
        assert '"success": false' in out2


def test_schemas_are_well_formed():
    for s in schemas.ALL:
        assert s["parameters"]["type"] == "object"
        assert len(s["description"]) > 80
        for req in s["parameters"].get("required", []):
            assert req in s["parameters"]["properties"]


def test_hint_hook_once_per_session_and_only_for_game_work():
    ctx = FakeCtx()
    _load.load().register(ctx)
    hook = ctx.hooks["pre_llm_call"]
    assert hook(session_id="a", user_message="summarize this pdf") is None
    first = hook(session_id="a", user_message="add an enemy hit reaction to my Godot game")
    assert "game-audio" in first["context"]
    assert hook(session_id="a", user_message="now the player jump in the game") is None


def test_import_is_lightweight():
    code = (
        "import sys; sys.path.insert(0, %r); import _load; _load.load(); "
        "heavy = [m for m in ('torch', 'numpy', 'mcp', 'tensorflow') if m in sys.modules]; print(heavy)"
    ) % str(_load.PLUGIN_DIR / "tests")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"
