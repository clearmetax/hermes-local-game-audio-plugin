"""Documentation completeness: every tool, parameter, setting, error code and
source file is documented, links resolve, versions agree, install scripts copy
every runtime file."""

import re
import subprocess
import sys
from pathlib import Path

import _load
import pytest
from hlga_under_test import errors, schemas, settings

ROOT = _load.PLUGIN_DIR
DOCS = ROOT / "docs"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def all_markdown() -> list[Path]:
    return [p for p in ROOT.rglob("*.md") if ".venv" not in p.parts and "node_modules" not in p.parts]


def test_tool_reference_is_generated_and_current():
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_tool_docs.py"), "--check"], capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr


def test_every_parameter_has_a_description():
    missing = []
    for s in schemas.ALL:
        for k, p in s["parameters"]["properties"].items():
            if not p.get("description"):
                missing.append(f"{s['name']}.{k}")
            for sk, sp in p.get("properties", {}).items():
                if not sp.get("description"):
                    missing.append(f"{s['name']}.{k}.{sk}")
            for sk, sp in p.get("items", {}).get("properties", {}).items():
                if not sp.get("description"):
                    missing.append(f"{s['name']}.{k}[].{sk}")
    assert not missing


def test_every_tool_is_documented_everywhere_it_should_be():
    names = [s["name"] for s in schemas.ALL]
    for doc in ("docs/TOOLS.md", "README.md", "skills/game-audio/SKILL.md"):
        text = read(doc)
        assert [n for n in names if f"`{n}`" not in text] == [], doc


def test_every_setting_documented_and_in_manifest():
    conf = read("docs/CONFIGURATION.md")
    assert [k for k in settings.DEFAULTS if f"`{k}`" not in conf] == []
    manifest_keys = re.findall(r"^  ([a-z_]+):\n    type:", read("plugin.yaml"), re.M)
    assert sorted(manifest_keys) == sorted(settings.DEFAULTS)


def test_every_error_code_documented():
    text = read("docs/TROUBLESHOOTING.md")
    codes = [v for k, v in vars(errors).items() if k.isupper() and isinstance(v, str)]
    assert [c for c in codes if f"`{c}`" not in text] == []


def test_every_source_file_in_the_layout_table():
    dev = read("docs/DEVELOPMENT.md")
    layout = dev[dev.index("## Repository layout"):dev.index("## Running the tests")]
    files = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
             if "tests" not in p.parts and "__pycache__" not in p.parts and "examples" not in p.parts]
    files += [p.relative_to(ROOT).as_posix() for p in (ROOT / "scripts").iterdir() if p.is_file()]
    undocumented = []
    for f in files:
        name = Path(f).name
        stem_variants = {f"`{f}`", f"`{name}`", f"{f}`"}
        if not any(v in layout for v in stem_variants):
            undocumented.append(f)
    assert undocumented == []


def test_install_scripts_copy_every_runtime_file():
    runtime = sorted({p.relative_to(ROOT).parts[0] for p in ROOT.rglob("*.py")
                      if not set(p.relative_to(ROOT).parts) & {"tests", "examples", "scripts", "__pycache__"}})
    runtime += ["plugin.yaml", "skills"]
    for script in ("scripts/install.sh", "scripts/install.ps1"):
        text = read(script)
        missing = [r for r in runtime if (f'"{r}"' not in text and f" {r} " not in text and f" {r};" not in text)]
        assert missing == [], (script, missing)


def test_versions_agree():
    v = re.search(r"^version: (\S+)", read("plugin.yaml"), re.M).group(1)
    assert re.search(r'^version = "([^"]+)"', read("pyproject.toml"), re.M).group(1) == v
    assert re.search(r"^version: (\S+)", read("skills/game-audio/SKILL.md"), re.M).group(1) == v
    assert re.search(r"^## (\S+)", read("CHANGELOG.md"), re.M).group(1) == v


def _anchors(md: str) -> set[str]:
    out = set()
    for h in re.findall(r"^#{1,6} (.+)$", md, re.M):
        slug = re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
        out.add(slug)
    return out


@pytest.mark.parametrize("md", all_markdown(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_resolve(md):
    text = md.read_text(encoding="utf-8")
    bad = []
    for target in re.findall(r"\]\(([^)\s]+)\)", text):
        if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
            continue
        path, _, anchor = target.partition("#")
        dest = (md.parent / path).resolve() if path else md
        if not dest.exists():
            bad.append(target)
            continue
        if anchor and dest.suffix == ".md" and anchor not in _anchors(dest.read_text(encoding="utf-8")):
            bad.append(target)
    assert bad == []


def test_readme_links_every_doc():
    readme = read("README.md")
    docs = [p.relative_to(ROOT).as_posix() for p in DOCS.glob("*.md")]
    assert [d for d in docs if d not in readme] == []
