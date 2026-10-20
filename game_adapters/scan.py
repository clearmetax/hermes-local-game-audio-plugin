"""Bounded, read-only project walking and source heuristics shared by adapters."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

AUDIO_EXTS = {".wav", ".ogg", ".mp3", ".flac", ".opus", ".aiff", ".aif", ".m4a"}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".godot", ".import", ".mono", "Library", "Temp", "Logs", "obj", "Obj",
    "Intermediate", "Saved", "Binaries", "DerivedDataCache", "node_modules", "build", "dist",
    "target", ".venv", "venv", "__pycache__", ".idea", ".vs", ".vscode", "export", "exports",
}
MAX_FILES = 25_000
MAX_TEXT_BYTES = 1_500_000

CODE_EXTS = {".gd", ".cs", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".lua", ".py", ".rs", ".gml", ".cpp", ".h", ".hpp", ".c"}

# event keyword -> (sound category, suggested description)
EVENT_KEYWORDS: list[tuple[str, str, str]] = [
    (r"take_?damage|on_?hit|got_?hit|_hit\b|^hit\b|hurt|damage", "hit", "short punchy hit impact, game sound effect"),
    (r"die\b|death|_died|kill(ed)?\b", "death", "short creature defeat sound, descending, game sound effect"),
    (r"jump", "jump", "quick light jump whoosh, game sound effect"),
    (r"land(ed|ing)?\b", "land", "soft landing thud on ground, game sound effect"),
    (r"shoot|fire\b|_fire|attack|swing|slash", "attack", "fast weapon attack swing, game sound effect"),
    (r"pick_?up|collect|coin|gem", "pickup", "bright cheerful item pickup chime, game sound effect"),
    (r"explod|explosion|detonat", "explosion", "punchy explosion with debris, game sound effect"),
    (r"pressed|click|button", "ui_click", "crisp soft UI button click, game sound effect"),
    (r"victory|win\b|level_?complete|stage_?clear", "victory", "short triumphant victory jingle sting, game sound effect"),
    (r"game_?over|lose\b|defeat", "game_over", "short sad game over sting, game sound effect"),
    (r"door|open(ed)?\b", "door", "wooden door opening creak, game sound effect"),
    (r"dash|dodge|roll\b", "dash", "fast air whoosh dash, game sound effect"),
    (r"heal", "heal", "gentle magical healing shimmer, game sound effect"),
    (r"level_?up|power_?up|upgrade", "powerup", "rising sparkly power-up, game sound effect"),
    (r"spawn", "spawn", "short magical spawn pop, game sound effect"),
    (r"reload", "reload", "mechanical weapon reload click clack, game sound effect"),
    (r"footstep|_step\b", "footstep", "single footstep on stone, game sound effect"),
]
_BUILTIN_SKIP = re.compile(r"^_(ready|process|physics_process|input|unhandled_input|init|enter_tree|exit_tree|draw|notification)$")

PLAY_PATTERNS = re.compile(
    r"\.play\s*\(|PlayOneShot|PlayClipAtPoint|AudioStreamPlayer|AudioSource|play_sfx|_hlga_play_sfx|playSound|play_sound|"
    r"SoundManager|AudioManager|sfx\.|Sfx\.|\bsound\.play|\.sound\.add|love\.audio|pygame\.mixer|UGameplayStatics::PlaySound",
)


@dataclass
class Walk:
    files: list[Path] = field(default_factory=list)
    truncated: bool = False
    skipped_links: int = 0


def walk(root: Path, max_files: int = MAX_FILES) -> Walk:
    out = Walk()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or d.startswith("."):
                continue
            if (base / d).is_symlink():
                out.skipped_links += 1
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            p = base / name
            if p.is_symlink():
                out.skipped_links += 1
                continue
            out.files.append(p)
            if len(out.files) >= max_files:
                out.truncated = True
                return out
    return out


def read_text(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return None
        return path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


_FUNC_PATTERNS = {
    ".gd": re.compile(r"^(?P<indent>[ \t]*)(?:static\s+)?func\s+(?P<name>[A-Za-z_]\w*)\s*\(", re.M),
    ".cs": re.compile(r"^(?P<indent>[ \t]*)(?:(?:public|private|protected|internal|static|virtual|override|async|void|IEnumerator|Task|\w+)\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{]*\)\s*(?:\{|$)", re.M),
    ".py": re.compile(r"^(?P<indent>[ \t]*)(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(", re.M),
    ".lua": re.compile(r"^(?P<indent>[ \t]*)(?:local\s+)?function\s+(?:[\w.:]+[.:])?(?P<name>[A-Za-z_]\w*)\s*\(", re.M),
    ".js": re.compile(r"^(?P<indent>[ \t]*)(?:(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(|(?P<mname>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{)", re.M),
}
for _ext in (".ts", ".tsx", ".jsx", ".mjs"):
    _FUNC_PATTERNS[_ext] = _FUNC_PATTERNS[".js"]
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "constructor"}


@dataclass
class FunctionInfo:
    name: str
    line: int
    body: str


def functions_in(text: str, ext: str) -> list[FunctionInfo]:
    pat = _FUNC_PATTERNS.get(ext)
    if not pat:
        return []
    matches = list(pat.finditer(text))
    out: list[FunctionInfo] = []
    for i, m in enumerate(matches):
        name = m.groupdict().get("name") or m.groupdict().get("mname")
        if not name or name in _JS_KEYWORDS:
            continue
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end][:6000]
        out.append(FunctionInfo(name, text.count("\n", 0, m.start()) + 1, body))
    return out


def classify_event(name: str) -> tuple[str, str] | None:
    if _BUILTIN_SKIP.match(name):
        return None
    low = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()  # CamelCase -> snake
    for pattern, category, desc in EVENT_KEYWORDS:
        if re.search(pattern, low):
            return category, desc
    return None


def naming_style(stems: list[str]) -> str:
    if not stems:
        return "snake_case"
    counts = {"snake_case": 0, "kebab-case": 0, "PascalCase": 0, "camelCase": 0}
    for s in stems:
        if "_" in s and s.lower() == s:
            counts["snake_case"] += 1
        elif "-" in s:
            counts["kebab-case"] += 1
        elif s[:1].isupper():
            counts["PascalCase"] += 1
        elif any(c.isupper() for c in s):
            counts["camelCase"] += 1
        else:
            counts["snake_case"] += 1
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def apply_style(stem: str, style: str) -> str:
    words = [w for w in re.split(r"[_\-\s]+", stem) if w]
    if style == "kebab-case":
        return "-".join(words)
    if style == "PascalCase":
        return "".join(w.capitalize() for w in words)
    if style == "camelCase":
        return words[0] + "".join(w.capitalize() for w in words[1:]) if words else stem
    return "_".join(words)
