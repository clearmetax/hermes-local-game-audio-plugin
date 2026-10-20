"""Objective quality analysis for game audio assets.

Produces measurements (loudness, peaks, tempo, spectrum, stereo, edges, loop
seam) and a list of concrete issues, each with a severity, an explanation and
whether mastering can fix it. ``score`` (0-100) ranks candidate generations:
it rewards technically clean audio that fits the requested kind, duration,
tempo and loop behaviour. It does not judge musical taste; the optional CLAP
score (semantic.py) measures how well audio matches the text.
"""

from __future__ import annotations

from typing import Any

from . import dsp

KINDS = ("sfx", "ui", "ambience", "music", "stinger")


def _issue(code: str, severity: str, message: str, penalty: float, fixable: bool) -> dict[str, Any]:
    return {"code": code, "severity": severity, "message": message, "penalty": penalty, "fixable_by_mastering": fixable}


def _clip_runs(a: dsp.Audio, level: float = 0.9995, min_run: int = 3) -> int:
    runs = 0
    for c in a.channels:
        run = 0
        for v in c:
            if v >= level or v <= -level:
                run += 1
                if run == min_run:
                    runs += 1
            else:
                run = 0
    return runs


def _silent_ratio(a: dsp.Audio, threshold_db: float = -60.0, block_s: float = 0.05) -> float:
    m = dsp.mono(a)
    blk = max(1, int(block_s * a.rate))
    thr = dsp.undb(threshold_db)
    total = silent = 0
    for s in range(0, len(m) - blk + 1, blk):
        total += 1
        if dsp.rms(m, s, s + blk) < thr:
            silent += 1
    return silent / total if total else 1.0


def _tail_is_abrupt(a: dsp.Audio) -> bool:
    m = dsp.mono(a)
    n = len(m)
    w = max(16, int(0.01 * a.rate))
    if n < 10 * w:
        return False
    body = max(dsp.rms(m, s, s + w) for s in range(0, n - w, w))
    last = dsp.rms(m, n - w, n)
    return body > 0 and dsp.db(last / body) > -24


def _attack_ms(a: dsp.Audio) -> float | None:
    m = dsp.mono(a)
    pk = max((abs(v) for v in m), default=0.0)
    if pk <= 0:
        return None
    first = next((i for i, v in enumerate(m) if abs(v) > pk * 0.05), None)
    top = next((i for i, v in enumerate(m) if abs(v) > pk * 0.9), None)
    if first is None or top is None:
        return None
    return round((top - first) / a.rate * 1000, 1)


def analyze(
    a: dsp.Audio,
    *,
    kind: str = "sfx",
    expected_seconds: float | None = None,
    bpm: float | None = None,
    loop: bool = False,
    loop_info: dict | None = None,
) -> dict[str, Any]:
    if kind not in KINDS:
        kind = "sfx"
    sample_peak = dsp.peak(a)
    tp = dsp.true_peak(a)
    lufs = dsp.integrated_loudness(a)
    st_max = dsp.max_short_term_loudness(a)
    first, last = dsp.edge_silence(a, -55.0)
    lead = first / a.rate if last >= first else a.seconds
    trail = (a.frames - 1 - last) / a.rate if last >= first else a.seconds
    usable = max(0.0, (last - first + 1) / a.rate) if last >= first else 0.0
    corr = dsp.stereo_correlation(a)
    spec = dsp.spectral_profile(a)
    dc = dsp.dc_offset(a)
    clips = _clip_runs(a)
    silent = _silent_ratio(a)
    tempo, tempo_conf = (None, 0.0)
    if kind in ("music", "stinger") or bpm:
        tempo, tempo_conf = dsp.estimate_tempo(a) if a.seconds >= 4 else (None, 0.0)
    metrics: dict[str, Any] = {
        "duration_seconds": round(a.seconds, 3),
        "usable_seconds": round(usable, 3),
        "sample_rate": a.rate,
        "channels": a.n_channels,
        "integrated_lufs": round(lufs, 1),
        "max_short_term_lufs": round(st_max, 1),
        "sample_peak_dbfs": round(dsp.db(sample_peak), 2),
        "true_peak_dbtp": round(dsp.db(tp), 2),
        "crest_db": round(dsp.db(sample_peak) - lufs, 1) if lufs > -100 else None,
        "leading_silence_seconds": round(lead, 3),
        "trailing_silence_seconds": round(trail, 3),
        "silent_ratio": round(silent, 3),
        "dc_offset": round(dc, 5),
        "clipped_runs": clips,
        "stereo_correlation": None if corr is None else round(corr, 3),
        "spectrum": spec,
        "attack_ms": _attack_ms(a) if kind in ("sfx", "ui") else None,
        "estimated_bpm": tempo,
        "tempo_confidence": tempo_conf,
    }
    if tempo and bpm:
        rel = min(((m, abs(tempo * m - bpm)) for m in (1.0, 2.0, 0.5, 1.5, 2 / 3)), key=lambda t: t[1])[0]
        metrics["tempo_matches_request"] = dsp.bpm_matches(tempo, bpm)
        metrics["tempo_relation"] = {1.0: "same", 2.0: "half-time", 0.5: "double-time", 1.5: "triplet-feel", 2 / 3: "dotted-feel"}[rel]
    issues: list[dict[str, Any]] = []
    if sample_peak <= 0:
        issues.append(_issue("silent", "error", "File is silent.", 100, False))
    if clips:
        issues.append(_issue("clipping", "error", f"{clips} clipped runs (digital distortion baked into the waveform).", min(30, 8 + clips), False))
    if dsp.db(tp) > -0.3:
        issues.append(_issue("true_peak_over", "warning", f"True peak {dsp.db(tp):.1f} dBTP; may distort after encoding/resampling.", 3, True))
    if dc > 0.01:
        issues.append(_issue("dc_offset", "warning", f"DC offset {dc:.3f}.", 3, True))
    if corr is not None and corr < -0.2:
        issues.append(_issue("phase", "error", f"Stereo correlation {corr:.2f}: channels partly cancel in mono.", 15, False))
    if lufs < -45 and sample_peak > 0:
        issues.append(_issue("too_quiet", "warning", f"Very low level ({lufs:.0f} LUFS).", 5, True))
    if kind in ("sfx", "ui"):
        if lead > 0.12:
            issues.append(_issue("late_start", "warning", f"Sound starts {lead:.2f}s late; feels laggy when triggered.", 8, True))
        if expected_seconds and usable < 0.35 * min(expected_seconds, 1.0):
            issues.append(_issue("too_short", "warning", f"Only {usable:.2f}s of audible content.", 10, False))
        if silent > 0.6 and a.seconds > 1.0:
            issues.append(_issue("mostly_silent", "warning", f"{silent:.0%} of the file is silence.", 6, True))
    if kind in ("music", "ambience"):
        if silent > 0.25:
            issues.append(_issue("gaps", "error", f"{silent:.0%} of the track is near-silent.", 25, False))
        if expected_seconds and a.seconds < 0.8 * expected_seconds and not loop:
            issues.append(_issue("too_short", "warning", f"{a.seconds:.1f}s instead of ~{expected_seconds:.0f}s.", 10, False))
        if kind == "music" and spec["flatness"] > 0.5:
            issues.append(_issue("noisy", "warning", "Spectrum is noise-like for music (possible failed generation).", 12, False))
    if kind in ("music", "stinger") and bpm:
        if tempo and not dsp.bpm_matches(tempo, bpm) and tempo_conf >= 0.3:
            issues.append(_issue("tempo_mismatch", "warning", f"Measured ~{tempo:.0f} BPM vs requested {bpm:.0f}.", 12, False))
        elif not tempo or tempo_conf < 0.3:
            issues.append(_issue("tempo_unclear", "info", "Tempo could not be measured reliably.", 2, False))
    if not loop and kind != "ambience" and _tail_is_abrupt(a):
        issues.append(_issue("abrupt_end", "warning", "Audio ends abruptly (cut off mid-sound).", 8, True))
    if loop:
        seam = (loop_info or {}).get("seam") or dsp.loop_seam(a, musical=kind in ("music", "stinger"))
        metrics["loop_seam"] = seam
        if seam["score"] < 60:
            issues.append(_issue("loop_seam", "warning", f"Loop seam is audible (score {seam['score']:.0f}/100).", 15, False))
    raw_score = 100 - sum(i["penalty"] for i in issues)
    # Fixable issues matter less when ranking candidates that will be mastered anyway.
    rank_score = 100 - sum(i["penalty"] * (0.25 if i["fixable_by_mastering"] else 1.0) for i in issues)
    return {
        "kind": kind,
        "metrics": metrics,
        "issues": issues,
        "score": max(0, round(raw_score)),
        "rank_score": max(0.0, round(rank_score, 1)),
        "verdict": "good" if raw_score >= 85 else ("acceptable" if raw_score >= 65 else "poor"),
    }
