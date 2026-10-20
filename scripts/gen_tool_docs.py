"""Regenerate the parameter tables in docs/TOOLS.md from schemas.py.

Usage:  python scripts/gen_tool_docs.py          (rewrite docs/TOOLS.md)
        python scripts/gen_tool_docs.py --check  (exit 1 if it is out of date)
Content between <!-- BEGIN:<tool> --> and <!-- END:<tool> --> is generated;
everything else in docs/TOOLS.md is hand-written and preserved.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "TOOLS.md"


def load_schemas():
    spec = importlib.util.spec_from_file_location("hlga_schemas", ROOT / "schemas.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # schemas.py has no imports from the package
    return mod.ALL


def _type(p: dict) -> str:
    if "enum" in p:
        return " \\| ".join(f"`{v}`" for v in p["enum"])
    if "anyOf" in p:
        return " or ".join(_type(x) for x in p["anyOf"])
    t = p.get("type", "any")
    if t == "array":
        items = p.get("items", {})
        return f"array of {items.get('type', 'object')}"
    return t


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def table(schema: dict) -> str:
    props = schema["parameters"].get("properties", {})
    req = set(schema["parameters"].get("required", []))
    lines = ["| Parameter | Type | Required | Description |", "|---|---|---|---|"]
    for name, p in props.items():
        lines.append(f"| `{name}` | {_type(p)} | {'yes' if name in req else ''} | {_cell(p.get('description', ''))} |")
        if p.get("type") == "object" and p.get("properties"):
            for sub, sp in p["properties"].items():
                lines.append(f"| `{name}.{sub}` | {_type(sp)} | | {_cell(sp.get('description', ''))} |")
        if p.get("type") == "array" and p.get("items", {}).get("properties"):
            ireq = set(p["items"].get("required", []))
            for sub, sp in p["items"]["properties"].items():
                lines.append(f"| `{name}[].{sub}` | {_type(sp)} | {'yes' if sub in ireq else ''} | {_cell(sp.get('description', ''))} |")
    return "\n".join(lines)


def render(text: str) -> str:
    for s in load_schemas():
        name = s["name"]
        block = f"<!-- BEGIN:{name} -->\n{table(s)}\n<!-- END:{name} -->"
        pat = re.compile(rf"<!-- BEGIN:{name} -->.*?<!-- END:{name} -->", re.S)
        if not pat.search(text):
            raise SystemExit(f"docs/TOOLS.md has no marker block for {name}")
        text = pat.sub(lambda _m: block, text)
    return text


def main() -> None:
    current = DOC.read_text(encoding="utf-8")
    new = render(current)
    if "--check" in sys.argv:
        if new != current:
            print("docs/TOOLS.md is out of date: run python scripts/gen_tool_docs.py")
            raise SystemExit(1)
        print("docs/TOOLS.md is up to date")
        return
    DOC.write_text(new, encoding="utf-8")
    print("docs/TOOLS.md regenerated")


if __name__ == "__main__":
    main()
