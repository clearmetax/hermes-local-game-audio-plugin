"""Honest hardware / disk preflight (no third-party packages)."""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Agent Audio's own published planning guidance (not verified minimums).
GUIDANCE = {
    "windows_cpu": "25 GB+ free disk and a 32 GB-class RAM system recommended; models are ~7.36 GB.",
    "apple_silicon": "A short generation has been reported on a 24 GiB Mac with ~6.88 GB of MLX model files.",
    "linux": "Linux CPU inference is not yet a validated Agent Audio path; model-free CI only.",
}


def total_ram_bytes() -> int | None:
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return int(stat.ullTotalPhys)
            return None
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5, check=False)
            return int(out.stdout.strip()) if out.stdout.strip().isdigit() else None
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        return None
    return None


def disk_free_bytes(path: Path) -> int | None:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _gb(n: int | None) -> float | None:
    return None if n is None else round(n / (1024**3), 1)


def machine_report(data_dir: Path) -> dict[str, Any]:
    system = platform.system()
    machine = platform.machine().lower()
    ram = total_ram_bytes()
    free = disk_free_bytes(data_dir)
    if system == "Windows":
        key = "windows_cpu"
    elif system == "Darwin" and machine in ("arm64", "aarch64"):
        key = "apple_silicon"
    else:
        key = "linux"
    warnings: list[str] = []
    if ram is not None and ram < 15.5 * 1024**3:
        warnings.append("Less than 16 GB RAM: Stable Audio 3 Medium may fail or be very slow; the procedural backend still works.")
    if free is not None and free < 25 * 1024**3:
        warnings.append("Less than 25 GB free disk: the Agent Audio runtime + models may not fit.")
    return {
        "os": f"{system} {platform.release()}",
        "arch": machine,
        "cpu_count": os.cpu_count(),
        "ram_gb": _gb(ram),
        "free_disk_gb_at_data_dir": _gb(free),
        "agent_audio_guidance": GUIDANCE[key],
        "accelerators": "Agent Audio v0.1 uses CPU (TFLite) on Windows/Linux and MLX on Apple Silicon; CUDA/XPU are not enabled.",
        "warnings": warnings,
    }
