"""Filesystem safety primitives.

Rules enforced here:
* every project write stays inside the resolved project root;
* no path component under the root may be a symlink or Windows junction;
* filenames are sanitised to a conservative character set;
* publication never replaces an existing file (exclusive create / hardlink).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from . import errors as E

_REPARSE_POINT = 0x400
_SAFE_CHARS = re.compile(r"[^a-z0-9_\-]+")
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MIN_FREE_BYTES = 200 * 1024 * 1024  # Refuse to write when the disk is nearly full.

# Child processes (model server, CLAP scorer, Godot, ffmpeg) never need Hermes's
# own state or anyone's credentials, so neither is passed down to them.
_SECRET_MARKERS = ("API_KEY", "APIKEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY")


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment for a child process, minus Hermes state and secrets.

    Drops every ``HERMES_*`` variable (so no approval/YOLO/non-interactive mode or
    profile state is inherited) and any variable whose name looks like a
    credential (``*_API_KEY``, ``*_TOKEN``, ``*_SECRET``...).
    """
    env = dict(os.environ if base is None else base)
    for name in list(env):
        upper = name.upper()
        if upper.startswith("HERMES_") or any(marker in upper for marker in _SECRET_MARKERS):
            del env[name]
    return env


def is_link(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def resolve_project_root(raw: str, allowed_roots: list[str] | None = None) -> Path:
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise E.AudioPluginError(E.INVALID_INPUT, "project_path must be a non-empty path string")
    candidate = Path(raw.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if is_link(candidate):
        raise E.AudioPluginError(E.PATH_REJECTED, f"Project root is a link; pass the real folder: {candidate}")
    root = candidate.resolve()
    if not root.is_dir():
        raise E.AudioPluginError(E.PROJECT_NOT_FOUND, f"Project folder not found: {root}")
    if allowed_roots:
        allowed = [Path(a).expanduser().resolve() for a in allowed_roots]
        if not any(root == a or a in root.parents for a in allowed):
            raise E.AudioPluginError(
                E.PATH_REJECTED,
                f"{root} is outside allowed_project_roots",
                hint="Add the folder to the plugin's allowed_project_roots setting.",
            )
    return root


def _reject_drive_or_absolute(rel: str) -> None:
    if PurePosixPath(rel).is_absolute() or PureWindowsPath(rel).is_absolute() or PureWindowsPath(rel).drive:
        raise E.AudioPluginError(E.PATH_REJECTED, f"Expected a project-relative path, got: {rel}")


def to_relative(root: Path, raw: str) -> str:
    """Accept res:// paths, project-relative paths, or absolute paths inside root."""
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise E.AudioPluginError(E.INVALID_INPUT, "path must be a non-empty string")
    value = raw.strip()
    if value.startswith("res://"):
        value = value[len("res://"):]
    p = Path(value).expanduser()
    if p.is_absolute():
        try:
            value = str(p.resolve().relative_to(root))
        except ValueError:
            raise E.AudioPluginError(E.PATH_REJECTED, f"Path escapes the project root: {raw}") from None
    _reject_drive_or_absolute(value)
    parts = [part for part in re.split(r"[\\/]+", value) if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise E.AudioPluginError(E.PATH_REJECTED, f"Path traversal is not allowed: {raw}")
    return "/".join(parts)


def resolve_inside(root: Path, rel: str, *, must_exist: bool = False) -> Path:
    """Resolve ``rel`` under ``root``, rejecting traversal and link components."""
    rel = to_relative(root, rel)
    current = root
    for part in [p for p in rel.split("/") if p]:
        current = current / part
        if is_link(current):
            raise E.AudioPluginError(E.PATH_REJECTED, f"Refusing to follow a link inside the project: {current}")
    resolved = current.resolve() if current.exists() else current
    if resolved != root and root not in resolved.parents:
        raise E.AudioPluginError(E.PATH_REJECTED, f"Path escapes the project root: {rel}")
    if must_exist and not resolved.exists():
        raise E.AudioPluginError(E.INVALID_INPUT, f"File not found in project: {rel}")
    return resolved


def safe_stem(raw: str, fallback: str = "sfx") -> str:
    text = unicodedata.normalize("NFKD", str(raw or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    if text.endswith(".wav"):
        text = text[:-4]
    text = _SAFE_CHARS.sub("_", text.replace(" ", "_")).strip("_-")
    text = re.sub(r"_+", "_", text)[:64].strip("_-")
    if not text or text in _WINDOWS_RESERVED:
        text = fallback
    return text


def unique_path(directory: Path, stem: str, ext: str = ".wav") -> Path:
    candidate = directory / f"{stem}{ext}"
    n = 2
    while os.path.lexists(candidate):
        candidate = directory / f"{stem}_{n:02d}{ext}"
        n += 1
        if n > 999:
            raise E.AudioPluginError(E.FILE_EXISTS, f"Too many files named {stem}* in {directory}")
    return candidate


def ensure_free_space(directory: Path, needed: int = 0) -> None:
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return
    if free < MIN_FREE_BYTES + needed:
        raise E.AudioPluginError(
            E.OUT_OF_SPACE,
            f"Only {free // (1024 * 1024)} MB free on the volume holding {directory}",
            hint="Free some disk space or choose another output folder.",
        )


def make_dirs_inside(root: Path, directory: Path) -> None:
    """mkdir -p, but refuse to create through links and never leave the root."""
    if directory != root and root not in directory.parents:
        raise E.AudioPluginError(E.PATH_REJECTED, f"Directory escapes the project root: {directory}")
    rel_parts = directory.relative_to(root).parts
    current = root
    for part in rel_parts:
        current = current / part
        if is_link(current):
            raise E.AudioPluginError(E.PATH_REJECTED, f"Refusing to create files through a link: {current}")
        if not current.exists():
            current.mkdir()
        elif not current.is_dir():
            raise E.AudioPluginError(E.PATH_REJECTED, f"Expected a folder but found a file: {current}")


def publish_exclusive(data: bytes, destination: Path) -> Path:
    """Write ``data`` to ``destination`` atomically, failing if it already exists."""
    if os.path.lexists(destination):
        raise E.AudioPluginError(E.FILE_EXISTS, f"Refusing to overwrite existing file: {destination}")
    ensure_free_space(destination.parent, len(data))
    fd, tmp_name = tempfile.mkstemp(prefix=".hlga-", suffix=".tmp", dir=destination.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, destination)  # Atomic and never replaces an existing entry.
        except FileExistsError:
            raise E.AudioPluginError(E.FILE_EXISTS, f"Refusing to overwrite existing file: {destination}") from None
        except OSError:
            # Filesystems without hardlinks (FAT/exFAT, some network shares).
            try:
                out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
            except FileExistsError:
                raise E.AudioPluginError(E.FILE_EXISTS, f"Refusing to overwrite existing file: {destination}") from None
            with os.fdopen(out, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        tmp.unlink(missing_ok=True)
    return destination


def atomic_replace_text(path: Path, expected: str, updated: str, encoding: str = "utf-8") -> None:
    """Replace a text file only if it still holds ``expected`` (detects concurrent edits)."""
    current = path.read_bytes().decode(encoding)
    if current != expected:
        raise E.AudioPluginError(E.CONFLICT, f"{path.name} changed while the edit was being prepared; nothing was written")
    fd, tmp_name = tempfile.mkstemp(prefix=".hlga-", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(updated.encode(encoding))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
