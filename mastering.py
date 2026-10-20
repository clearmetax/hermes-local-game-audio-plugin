"""Game-ready mastering profiles.

Targets are sensible starting points for game assets (individual assets are
balanced again in the engine's mixer):

| kind     | level target                    | ceiling   | edges                         |
|----------|---------------------------------|-----------|-------------------------------|
| sfx      | peak -1 dBTP                    | -1 dBTP   | trim, 2 ms in / 15 ms out     |
| ui       | -20 LUFS (peak -3 dBTP max)     | -3 dBTP   | trim, 1 ms in / 10 ms out     |
| stinger  | -16 LUFS                        | -1 dBTP   | trim head, natural/forced tail|
| music    | -16 LUFS                        | -1 dBTP   | trim head; loop or fade tail  |
| ambience | -24 LUFS                        | -3 dBTP   | loop or long fades            |

Loudness normalization is followed by a lookahead limiter when the gain would
push the true peak over the ceiling, then 16-bit TPDF dither on export.
Looping assets get a bar-aligned (when the BPM is known), phase-matched,
equal-power crossfaded loop and an embedded ``smpl`` loop chunk; their edges
are never faded (a fade would make the loop dip).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import dsp

PROFILES: dict[str, dict[str, Any]] = {
    "sfx": {"lufs": None, "peak_dbtp": -1.0, "ceiling_dbtp": -1.0, "fade_in": 0.002, "fade_out": 0.015, "trim_tail": True},
    "ui": {"lufs": -20.0, "peak_dbtp": None, "ceiling_dbtp": -3.0, "fade_in": 0.001, "fade_out": 0.010, "trim_tail": True},
    "stinger": {"lufs": -16.0, "peak_dbtp": None, "ceiling_dbtp": -1.0, "fade_in": 0.002, "fade_out": 0.25, "trim_tail": True},
    "music": {"lufs": -16.0, "peak_dbtp": None, "ceiling_dbtp": -1.0, "fade_in": 0.005, "fade_out": 2.0, "trim_tail": False},
    "ambience": {"lufs": -24.0, "peak_dbtp": None, "ceiling_dbtp": -3.0, "fade_in": 1.0, "fade_out": 2.0, "trim_tail": False},
}


@dataclass
class MasterOptions:
    kind: str = "sfx"
    enabled: bool = True
    loop: bool = False
    bpm: float | None = None
    beats_per_bar: int = 4
    loop_seconds: float | None = None
    crossfade_seconds: float | None = None
    target_lufs: float | None = None
    peak_dbtp: float | None = None
    ceiling_dbtp: float | None = None
    trim_silence: bool = True
    fade_out_seconds: float | None = None
    mono: bool = False
    remove_dc: bool = True
    normalize: bool = True

    @classmethod
    def from_args(cls, kind: str, raw: Any, *, loop: bool = False, bpm: float | None = None,
                  beats_per_bar: int = 4, loop_seconds: float | None = None) -> "MasterOptions":
        o = cls(kind=kind, loop=loop, bpm=bpm, beats_per_bar=beats_per_bar, loop_seconds=loop_seconds)
        if raw is False:
            o.enabled = False
            return o
        if isinstance(raw, dict):
            if raw.get("enabled") is False:
                o.enabled = False
                return o
            for key in ("target_lufs", "peak_dbtp", "ceiling_dbtp", "fade_out_seconds", "crossfade_seconds"):
                if key in raw and raw[key] is not None:
                    setattr(o, key, float(raw[key]))
            if "normalize_peak_db" in raw:  # backward compatible name
                if raw["normalize_peak_db"] is None:
                    o.normalize = False
                else:
                    o.peak_dbtp = float(raw["normalize_peak_db"])
            if raw.get("normalize") is False or ("target_lufs" in raw and raw["target_lufs"] is None and "peak_dbtp" not in raw):
                o.normalize = False
            for key in ("trim_silence", "mono", "remove_dc"):
                if key in raw:
                    setattr(o, key, bool(raw[key]))
            if "fade_out_ms" in raw and raw["fade_out_ms"] is not None:
                o.fade_out_seconds = float(raw["fade_out_ms"]) / 1000
        return o


def master(a: dsp.Audio, o: MasterOptions) -> tuple[dsp.Audio, list[str], dict[str, Any]]:
    """Return (mastered audio, human-readable steps, loop info or {})."""
    steps: list[str] = []
    loop_info: dict[str, Any] = {}
    if not o.enabled:
        if not o.loop:
            return a, ["mastering disabled (raw model output)"], loop_info
        # A loop was requested: still build it, but change nothing else.
        xf = o.crossfade_seconds if o.crossfade_seconds is not None else min(0.75 if o.kind == "music" else 1.5, max(0.05, a.seconds * 0.1))
        looped, loop_info = dsp.make_loop(a, loop_seconds=o.loop_seconds, crossfade_s=xf, bpm=o.bpm, beats_per_bar=o.beats_per_bar)
        return looped, ["mastering disabled (levels untouched); seamless loop still built",
                        f"built seamless loop: {loop_info['loop_seconds']:.2f}s, seam score {loop_info['seam']['score']:.0f}/100"], loop_info
    p = PROFILES.get(o.kind, PROFILES["sfx"])
    out = a
    if o.remove_dc and dsp.dc_offset(out) > 0.002:
        out = dsp.remove_dc(out)
        steps.append("removed DC offset")
    if o.mono and out.n_channels > 1:
        out = dsp.to_mono(out)
        steps.append("downmixed to mono")
    if o.trim_silence:
        trim_tail = p["trim_tail"] and not o.loop
        out2, cut_head, cut_tail = dsp.trim(out, head=True, tail=trim_tail,
                                            threshold_db=-55.0 if o.kind in ("sfx", "ui", "stinger") else -60.0)
        if cut_head >= 0.005 or cut_tail >= 0.005:
            steps.append(f"trimmed silence ({cut_head:.2f}s head, {cut_tail:.2f}s tail)")
        out = out2
    if o.loop:
        xf = o.crossfade_seconds
        if xf is None:
            xf = 0.75 if o.kind == "music" else 1.5
            xf = min(xf, max(0.05, out.seconds * 0.1))
        out, loop_info = dsp.make_loop(out, loop_seconds=o.loop_seconds, crossfade_s=xf, bpm=o.bpm,
                                       beats_per_bar=o.beats_per_bar)
        desc = f"built seamless loop: {loop_info['loop_seconds']:.2f}s"
        if loop_info.get("bars"):
            desc += f" = {loop_info['bars']} bars at {o.bpm:g} BPM"
        desc += f", {loop_info['crossfade_seconds']:.2f}s equal-power crossfade, seam score {loop_info['seam']['score']:.0f}/100"
        steps.append(desc)
    # level
    target_lufs = o.target_lufs if o.target_lufs is not None else p["lufs"]
    peak_target = o.peak_dbtp if o.peak_dbtp is not None else p["peak_dbtp"]
    ceiling = o.ceiling_dbtp if o.ceiling_dbtp is not None else p["ceiling_dbtp"]
    if o.peak_dbtp is not None and o.target_lufs is None:
        target_lufs = None
    tp = dsp.true_peak(out)
    if tp <= 0:
        return out, steps + ["silent input; level unchanged"], loop_info
    if not o.normalize:
        target_lufs = peak_target = None
    if target_lufs is not None:
        cur = dsp.integrated_loudness(out)
        if cur > -100:
            gain_db = max(-30.0, min(30.0, target_lufs - cur))
            out = dsp.apply_gain(out, dsp.undb(gain_db))
            steps.append(f"loudness {cur:.1f} -> {target_lufs:.1f} LUFS ({gain_db:+.1f} dB)")
    elif peak_target is not None:
        gain_db = max(-30.0, min(30.0, peak_target - dsp.db(tp)))
        out = dsp.apply_gain(out, dsp.undb(gain_db))
        steps.append(f"peak normalized to {peak_target:g} dBTP ({gain_db:+.1f} dB)")
    tp = dsp.true_peak(out)
    if dsp.db(tp) > ceiling + 0.05:
        # Sample-peak ceiling below the true-peak ceiling leaves headroom for inter-sample overs.
        sample_ceiling = dsp.undb(ceiling - 0.4)
        total_gr = 0.0
        for _ in range(3):
            out, gr = dsp.limit(out, sample_ceiling)
            total_gr += gr
            tp = dsp.true_peak(out)
            if dsp.db(tp) <= ceiling + 0.05:
                break
            sample_ceiling *= dsp.undb(-0.3)
        steps.append(f"limited to {ceiling:g} dBTP (up to {abs(total_gr):.1f} dB gain reduction)")
    if not o.loop:
        fi = p["fade_in"]
        fo = o.fade_out_seconds if o.fade_out_seconds is not None else p["fade_out"]
        if o.kind in ("music", "stinger", "ambience"):
            fo = min(fo, out.seconds * 0.15)
        fo = min(fo, out.seconds * 0.25)
        out = dsp.fade(out, fi, fo)
        steps.append(f"edge fades ({fi * 1000:.0f} ms in / {fo * 1000:.0f} ms out)")
    if target_lufs is not None:
        final = dsp.integrated_loudness(out)
        msg = f"final loudness {final:.1f} LUFS"
        if final < target_lufs - 1.0:
            msg += f" (target {target_lufs:g} not reachable within the {ceiling:g} dBTP ceiling: very peaky material)"
        steps.append(msg)
    steps.append("exported 16-bit PCM with TPDF dither" + (" + embedded loop points" if o.loop else ""))
    return out, steps, loop_info
