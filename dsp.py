"""Pure-Python DSP used for analysis and mastering (Hermes ships without numpy).

Everything works on ``Audio`` objects: per-channel ``array('d')`` sample
buffers in [-1, 1] plus a sample rate. Long files are analysed on decimated
copies where the measurement allows it, so a 2-minute stereo track is
processed in seconds.

References implemented here:
* ITU-R BS.1770-4 K-weighting, gated integrated loudness (LUFS), momentary /
  short-term maxima (filter constants as in libebur128/pyloudnorm);
* estimated true peak via 4x windowed-sinc oversampling around the loudest peaks;
* lookahead peak limiter, TPDF dither, equal-power loop crossfades.
"""

from __future__ import annotations

import cmath
import math
import operator
import random
from array import array
from collections import deque
from dataclasses import dataclass


@dataclass
class Audio:
    channels: list[array]
    rate: int

    @property
    def frames(self) -> int:
        return len(self.channels[0]) if self.channels else 0

    @property
    def seconds(self) -> float:
        return self.frames / self.rate if self.rate else 0.0

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    def copy(self) -> "Audio":
        return Audio([array("d", c) for c in self.channels], self.rate)

    def slice(self, start: int, end: int) -> "Audio":
        return Audio([c[start:end] for c in self.channels], self.rate)


def db(x: float, floor: float = -150.0) -> float:
    return 20 * math.log10(x) if x > 0 else floor


def undb(d: float) -> float:
    return 10 ** (d / 20)


def _scale(x, k: float) -> array:
    return array("d", map(k.__mul__, x))


def mono(a: Audio) -> array:
    if a.n_channels == 1:
        return a.channels[0]
    cached = getattr(a, "_mono", None)
    if cached is not None and len(cached) == a.frames:
        return cached
    chans = a.channels
    acc = array("d", map(operator.add, chans[0], chans[1]))
    for c in chans[2:]:
        acc = array("d", map(operator.add, acc, c))
    out = _scale(acc, 1.0 / a.n_channels)
    try:
        a._mono = out  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover
        pass
    return out


def decimate(x: array, factor: int) -> array:
    """Box-filter decimation; adequate for envelope/loudness-band measurements."""
    if factor <= 1:
        return x
    n = len(x) // factor
    acc = array("d", x[0: n * factor: factor])
    for k in range(1, factor):
        acc = array("d", map(operator.add, acc, x[k: n * factor: factor]))
    return _scale(acc, 1.0 / factor)


def peak(a: Audio) -> float:
    return max((max(max(c), -min(c)) for c in a.channels if len(c)), default=0.0)


def rms(x, start: int = 0, end: int | None = None) -> float:
    end = len(x) if end is None else end
    n = end - start
    if n <= 0:
        return 0.0
    s = 0.0
    for i in range(start, end):
        v = x[i]
        s += v * v
    return math.sqrt(s / n)


# ----------------------------------------------------------------- filters
def biquad(x, b0: float, b1: float, b2: float, a1: float, a2: float) -> array:
    y = array("d", bytes(8 * len(x)))
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x):
        o = b0 * v + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, o
        y[i] = o
    return y


def _k_weight_coeffs(fs: float) -> list[tuple[float, float, float, float, float]]:
    """BS.1770 K-weighting for any sample rate (same derivation as libebur128)."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / fs)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    shelf = ((Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0,
             2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0)
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / fs)
    a0 = 1 + K / Q + K * K
    hp = (1.0, -2.0, 1.0, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0)
    return [shelf, hp]


def _k_weighted_prefix(a: Audio) -> tuple[list[array], int]:
    """Per-channel prefix sums of K-weighted squared samples (cached on the Audio)."""
    key = (a.frames, a.rate, a.n_channels, id(a.channels[0]) if a.channels else 0)
    cached = getattr(a, "_kw", None)
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    rate = a.rate
    chans = a.channels
    # Full rate on purpose: the K-filter's high shelf weights content up to Nyquist,
    # so decimating would under-read bright material.
    coeffs = _k_weight_coeffs(rate)
    weighted = []
    for c in chans:
        y = c
        for co in coeffs:
            y = biquad(y, *co)
        sq = map(operator.mul, y, y)
        pre = array("d", [0.0])
        pre.extend(_accumulate(sq))
        weighted.append(pre)
    try:
        a._kw = (key, weighted, rate)  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover
        pass
    return weighted, rate


def _accumulate(it):
    import itertools

    return itertools.accumulate(it)


def _k_weighted_power_blocks(a: Audio, block_s: float, step_s: float) -> list[float]:
    """Mean-square K-weighted power per gating block, summed over channels."""
    weighted, rate = _k_weighted_prefix(a)
    n = len(weighted[0]) - 1
    blk = int(block_s * rate)
    step = max(1, int(step_s * rate))
    powers = []
    if n < blk:
        if n == 0:
            return []
        return [sum(p[n] / n for p in weighted)]
    for s in range(0, n - blk + 1, step):
        powers.append(sum((p[s + blk] - p[s]) / blk for p in weighted))
    return powers


def _lufs(power: float) -> float:
    return -0.691 + 10 * math.log10(power) if power > 0 else -150.0


def integrated_loudness(a: Audio) -> float:
    """BS.1770-4 gated integrated loudness in LUFS (-150 for silence)."""
    blocks = _k_weighted_power_blocks(a, 0.4, 0.1)
    if not blocks:
        return -150.0
    gated = [p for p in blocks if _lufs(p) > -70.0]
    if not gated:
        return -150.0
    rel = _lufs(sum(gated) / len(gated)) - 10.0
    gated2 = [p for p in gated if _lufs(p) > rel]
    if not gated2:
        return -150.0
    return _lufs(sum(gated2) / len(gated2))


def max_short_term_loudness(a: Audio) -> float:
    blocks = _k_weighted_power_blocks(a, 3.0, 0.5) if a.seconds >= 3 else _k_weighted_power_blocks(a, a.seconds, 1.0)
    return max((_lufs(p) for p in blocks), default=-150.0)


def max_momentary_loudness(a: Audio) -> float:
    return max((_lufs(p) for p in _k_weighted_power_blocks(a, 0.4, 0.1)), default=-150.0)


# --------------------------------------------------------------- true peak
def _sinc_table(phases: int = 4, taps: int = 16) -> list[list[float]]:
    table = []
    half = taps // 2
    for p in range(phases):
        frac = p / phases
        row = []
        for k in range(-half + 1, half + 1):
            t = k - frac
            s = 1.0 if t == 0 else math.sin(math.pi * t) / (math.pi * t)
            w = 0.5 * (1 + math.cos(math.pi * t / (half + 0.5)))  # Hann window
            row.append(s * w)
        table.append(row)
    return table


_TP_TABLE = _sinc_table()


def true_peak(a: Audio, max_candidates: int = 4000) -> float:
    """Estimated true peak (linear), 4x oversampled around the loudest samples."""
    best = 0.0
    taps = len(_TP_TABLE[0])
    half = taps // 2
    for c in a.channels:
        n = len(c)
        if n == 0:
            continue
        sp = max(max(c), -min(c))
        best = max(best, sp)
        if sp <= 0:
            continue
        thr = sp * 0.5
        loud = [i for i, v in enumerate(c) if (v >= thr or v <= -thr) and 0 < i < n - 1]
        cands = [i for i in loud if abs(c[i]) >= abs(c[i - 1]) and abs(c[i]) >= abs(c[i + 1])]
        if len(cands) > max_candidates:
            cands.sort(key=lambda i: -abs(c[i]))
            cands = cands[:max_candidates]
        for i in cands:
            for j in (i - 1, i):  # inter-sample positions on both sides of the peak
                for row in _TP_TABLE[1:]:
                    s = 0.0
                    base = j - half + 1
                    for k, w in enumerate(row):
                        idx = base + k
                        if 0 <= idx < n:
                            s += c[idx] * w
                    if abs(s) > best:
                        best = abs(s)
    return best


# --------------------------------------------------------------- dynamics
def apply_gain(a: Audio, gain: float) -> Audio:
    return Audio([array("d", (v * gain for v in c)) for c in a.channels], a.rate)


def limit(a: Audio, ceiling: float, lookahead_ms: float = 2.0, release_ms: float = 80.0) -> tuple[Audio, float]:
    """Lookahead brick-wall limiter with linked channels.

    gain = release_smooth(box_average(forward_sliding_min(needed_gain))). Every
    sample's box-average window only contains minima whose look-ahead covers
    that sample, so the output never exceeds ``ceiling`` (sample peak) while the
    gain changes smoothly (no clicks). Returns (audio, max gain reduction in dB).
    """
    n = a.frames
    if n == 0:
        return a, 0.0
    la = max(1, int(a.rate * lookahead_ms / 1000))
    rel = 1.0 - math.exp(-1.0 / max(1.0, a.rate * release_ms / 1000))
    chans = a.channels
    need = array("d", [1.0]) * n
    any_over = False
    for i in range(n):
        m = 0.0
        for c in chans:
            v = c[i]
            if v < 0:
                v = -v
            if v > m:
                m = v
        if m > ceiling:
            need[i] = ceiling / m
            any_over = True
    if not any_over:
        return a.copy(), 0.0
    # forward sliding minimum over [i, i+la)
    gmin = array("d", [1.0]) * n
    dq: deque[int] = deque()
    for i in range(n - 1, -1, -1):
        while dq and need[dq[-1]] >= need[i]:
            dq.pop()
        dq.append(i)
        while dq[0] >= i + la:
            dq.popleft()
        gmin[i] = need[dq[0]]
    # backward box average over (i-la, i]
    gavg = array("d", [1.0]) * n
    acc = 0.0
    for i in range(n):
        acc += gmin[i]
        if i >= la:
            acc -= gmin[i - la]
            gavg[i] = acc / la
        else:
            gavg[i] = (acc + (la - i - 1) * 1.0) / la
    # release smoothing (never above the safe envelope)
    g = 1.0
    worst = 1.0
    out = [array("d", bytes(8 * n)) for _ in chans]
    for i in range(n):
        target = gavg[i]
        g = target if target < g else g + (target - g) * rel
        if g > target:
            g = target
        if g < worst:
            worst = g
        for ch, c in enumerate(chans):
            out[ch][i] = c[i] * g
    return Audio(out, a.rate), db(worst)


def remove_dc(a: Audio, cutoff_hz: float = 15.0) -> Audio:
    r = 1.0 - 2 * math.pi * cutoff_hz / a.rate
    out = []
    for c in a.channels:
        y = array("d", bytes(8 * len(c)))
        px = py = 0.0
        for i, v in enumerate(c):
            o = v - px + r * py
            px, py = v, o
            y[i] = o
        out.append(y)
    return Audio(out, a.rate)


def dc_offset(a: Audio) -> float:
    return max((abs(sum(c) / len(c)) for c in a.channels if len(c)), default=0.0)


def fade(a: Audio, fade_in_s: float = 0.0, fade_out_s: float = 0.0, curve: str = "sine") -> Audio:
    out = a.copy()
    n = out.frames
    fi = min(n // 2, int(fade_in_s * a.rate))
    fo = min(n // 2, int(fade_out_s * a.rate))

    def shape(t: float) -> float:
        return math.sin(t * math.pi / 2) if curve == "sine" else t

    for c in out.channels:
        for i in range(fi):
            c[i] *= shape(i / fi)
        for i in range(fo):
            c[n - 1 - i] *= shape(i / fo)
    return out


def to_mono(a: Audio) -> Audio:
    return Audio([mono(a)], a.rate)


def edge_silence(a: Audio, threshold_db: float = -55.0) -> tuple[int, int]:
    """Return (first, last) frame above the threshold (relative to full scale)."""
    thr = undb(threshold_db)
    n = a.frames
    first, last = n, -1
    for c in a.channels:
        for i in range(n):
            if abs(c[i]) > thr:
                first = min(first, i)
                break
        for i in range(n - 1, -1, -1):
            if abs(c[i]) > thr:
                last = max(last, i)
                break
    return first, last


def trim(a: Audio, *, head: bool = True, tail: bool = True, threshold_db: float = -55.0,
         head_pad_s: float = 0.003, tail_pad_s: float = 0.04) -> tuple[Audio, float, float]:
    first, last = edge_silence(a, threshold_db)
    if last < first:
        return a, 0.0, 0.0
    start = max(0, first - int(head_pad_s * a.rate)) if head else 0
    end = min(a.frames, last + 1 + int(tail_pad_s * a.rate)) if tail else a.frames
    return a.slice(start, end), start / a.rate, (a.frames - end) / a.rate


# --------------------------------------------------------------- spectrum
def fft(re: list[float]) -> list[complex]:
    n = len(re)
    if n & (n - 1):
        raise ValueError("fft size must be a power of two")
    x = [complex(v, 0.0) for v in re]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            x[i], x[j] = x[j], x[i]
    size = 2
    while size <= n:
        w_step = cmath.exp(-2j * math.pi / size)
        half = size // 2
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(start, start + half):
                t = w * x[k + half]
                x[k + half] = x[k] - t
                x[k] = x[k] + t
                w *= w_step
        size <<= 1
    return x


def spectral_profile(a: Audio, frame: int = 2048, max_frames: int = 48) -> dict[str, float]:
    m = mono(a)
    n = len(m)
    if n < frame:
        m = array("d", m) + array("d", [0.0]) * (frame - n)
        n = frame
    hops = max(1, (n - frame) // max(1, max_frames - 1))
    starts = list(range(0, n - frame + 1, hops))[:max_frames]
    win = [0.5 - 0.5 * math.cos(2 * math.pi * i / (frame - 1)) for i in range(frame)]
    freq = a.rate / frame
    acc = [0.0] * (frame // 2)
    used = 0
    for s in starts:
        seg = m[s:s + frame]
        if rms(seg) < 1e-4:
            continue
        spec = fft([seg[i] * win[i] for i in range(frame)])
        for k in range(frame // 2):
            acc[k] += abs(spec[k]) ** 2
        used += 1
    if not used:
        return {"centroid_hz": 0.0, "rolloff85_hz": 0.0, "flatness": 0.0, "low_ratio": 0.0, "mid_ratio": 0.0, "high_ratio": 0.0}
    total = sum(acc) or 1e-30
    centroid = sum(k * freq * p for k, p in enumerate(acc)) / total
    cum, roll = 0.0, 0.0
    for k, p in enumerate(acc):
        cum += p
        if cum >= 0.85 * total:
            roll = k * freq
            break
    nz = [p for p in acc[1:] if p > 0]
    flat = math.exp(sum(math.log(p) for p in nz) / len(nz)) / (sum(nz) / len(nz)) if nz else 0.0
    low = sum(p for k, p in enumerate(acc) if k * freq < 250) / total
    high = sum(p for k, p in enumerate(acc) if k * freq >= 4000) / total
    return {"centroid_hz": round(centroid, 1), "rolloff85_hz": round(roll, 1), "flatness": round(flat, 4),
            "low_ratio": round(low, 3), "mid_ratio": round(1 - low - high, 3), "high_ratio": round(high, 3)}


def stereo_correlation(a: Audio) -> float | None:
    if a.n_channels < 2:
        return None
    left, right = a.channels[0], a.channels[1]
    step = max(1, a.frames // 400_000)
    sl = sr = slr = 0.0
    for i in range(0, a.frames, step):
        lv, rv = left[i], right[i]
        sl += lv * lv
        sr += rv * rv
        slr += lv * rv
    if sl <= 0 or sr <= 0:
        return None
    return slr / math.sqrt(sl * sr)


# --------------------------------------------------------------- rhythm
def onset_envelope(a: Audio, target_rate: int = 11025, hop: int = 128) -> tuple[list[float], float]:
    m = mono(a)
    factor = max(1, a.rate // target_rate)
    d = decimate(m, factor)
    sr = a.rate / factor
    env = []
    prev = 0.0
    for s in range(0, len(d) - hop, hop):
        e = math.log1p(1000 * rms(d, s, s + hop))
        env.append(max(0.0, e - prev))
        prev = e
    return env, sr / hop


def estimate_tempo(a: Audio, lo: float = 60.0, hi: float = 200.0) -> tuple[float | None, float]:
    """Return (bpm, confidence 0-1) from onset-envelope autocorrelation."""
    env, fps = onset_envelope(a)
    if len(env) < fps * 4:
        return None, 0.0
    mean = sum(env) / len(env)
    env = [v - mean for v in env]
    min_lag = int(fps * 60 / hi)
    max_lag = int(fps * 60 / lo) + 1
    n = len(env)
    energy = sum(v * v for v in env) or 1e-12
    scores = {}
    for lag in range(max(1, min_lag), min(max_lag, n // 2)):
        s = 0.0
        for i in range(n - lag):
            s += env[i] * env[i + lag]
        scores[lag] = s / energy
    if not scores:
        return None, 0.0

    def weighted(lag: int) -> float:
        bpm = 60 * fps / lag
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.9) ** 2)
        return scores[lag] * (0.6 + 0.4 * prior)

    best = max(scores, key=weighted)
    # parabolic refinement
    lag = float(best)
    if best - 1 in scores and best + 1 in scores:
        y0, y1, y2 = scores[best - 1], scores[best], scores[best + 1]
        denom = y0 - 2 * y1 + y2
        if denom:
            lag = best + 0.5 * (y0 - y2) / denom
    vals = sorted(scores.values())
    median = vals[len(vals) // 2]
    conf = max(0.0, min(1.0, (scores[best] - median) / (abs(scores[best]) + 1e-9)))
    return round(60 * fps / lag, 1), round(conf, 2)


def bpm_matches(measured: float | None, requested: float, tolerance: float = 0.04) -> bool:
    if not measured:
        return False
    for mult in (1.0, 2.0, 0.5, 1.5, 2 / 3):
        if abs(measured * mult - requested) / requested <= tolerance:
            return True
    return False


# --------------------------------------------------------------- loops
def _corr(x: array, i: int, y: array, j: int, n: int) -> float:
    s = sx = sy = 0.0
    for k in range(n):
        a = x[i + k]
        b = y[j + k]
        s += a * b
        sx += a * a
        sy += b * b
    return s / math.sqrt(sx * sy) if sx > 0 and sy > 0 else 0.0


def loop_seam(a: Audio, window_s: float = 0.02, probes: int = 300, musical: bool = False) -> dict[str, float]:
    """How audible the wrap (last sample -> first sample) is, relative to the audio itself.

    Compares the level change and waveform jump across the wrap with the same
    measures at ``probes`` ordinary positions inside the file. A seam that is no
    more discontinuous than a typical moment of the material scores ~100; one
    worse than 99% of internal moments scores near 0. Natural events (a drum on
    the downbeat) are therefore not mistaken for seams. With ``musical`` only
    level *drops* count (a loop that restarts on a downbeat naturally gets
    louder; one that cuts a sustained sound off gets quieter).
    """
    n = a.frames
    w = max(16, int(window_s * a.rate))
    if n < 8 * w:
        return {"level_jump_db": 0.0, "sample_jump": 0.0, "level_percentile": 0.0, "jump_percentile": 0.0, "score": 0.0}
    m = mono(a)

    def level_step(pos: int) -> float:
        before = rms(m, pos - w, pos) if pos >= w else rms(m, n - (w - pos), n)  # wrap-aware
        after = rms(m, pos, pos + w)
        d = db(before + 1e-7) - db(after + 1e-7)
        return max(0.0, d) if musical else abs(d)

    def jump(pos: int) -> float:
        prev = m[pos - 1] if pos > 0 else m[n - 1]
        return abs(m[pos] - prev)

    rnd = random.Random(7)
    positions = [rnd.randrange(w, n - w) for _ in range(probes)]
    lv = sorted(level_step(p) for p in positions)
    jp = sorted(jump(p) for p in positions)
    wrap_level = level_step(0)
    wrap_jump = jump(0)

    def percentile(sorted_vals: list[float], v: float) -> float:
        below = sum(1 for x in sorted_vals if x < v)
        return below / len(sorted_vals)

    pl = percentile(lv, wrap_level)
    pj = percentile(jp, wrap_jump)
    worst = max(pl, pj)
    score = 100.0 if worst <= 0.9 else max(0.0, 100.0 * (1.0 - (worst - 0.9) / 0.1))
    return {"level_jump_db": round(wrap_level, 2), "sample_jump": round(wrap_jump, 4),
            "level_percentile": round(pl, 3), "jump_percentile": round(pj, 3), "score": round(score, 1)}


def make_loop(a: Audio, *, loop_seconds: float | None = None, crossfade_s: float = 1.0,
              bpm: float | None = None, beats_per_bar: int = 4, search_s: float = 0.25) -> tuple[Audio, dict]:
    """Build a seamless loop: the material after the loop end is crossfaded into the head.

    The loop length is snapped to whole bars when ``bpm`` is given. Within
    ``search_s`` of the target the end point with the highest waveform
    correlation to the start is chosen, so the wrap lands in phase.
    """
    rate = a.rate
    n = a.frames
    xf = max(int(0.01 * rate), int(crossfade_s * rate))
    max_body = n - xf
    if max_body <= xf * 2:
        raise ValueError("audio too short to build a loop with this crossfade")
    if loop_seconds is None:
        loop_seconds = max_body / rate
    bars = None
    if bpm:
        bar_s = beats_per_bar * 60.0 / bpm
        whole = int((min(loop_seconds, max_body / rate) + 1e-6) // bar_s)
        if whole >= 1:
            bars = whole
            loop_seconds = bars * bar_s
    target = int(min(loop_seconds * rate, max_body))
    if target < 2 * xf:
        raise ValueError(f"loop length {target / rate:.2f}s must be at least twice the crossfade ({xf / rate:.2f}s)")
    m = mono(a)
    w = min(int(0.03 * rate), xf)
    search = int(search_s * rate) if bars is None else int(0.012 * rate)
    lo = max(xf * 2, target - search)
    hi = max(lo, min(max_body, target + search))
    step = max(1, (hi - lo) // 400)
    best_e, best_c = target, -2.0
    for e in range(lo, hi + 1, step):
        c = _corr(m, 0, m, e, w)
        if c > best_c:
            best_c, best_e = c, e
    if step > 1:
        for e in range(max(lo, best_e - step), min(hi, best_e + step) + 1):
            c = _corr(m, 0, m, e, w)
            if c > best_c:
                best_c, best_e = c, e
    e = best_e
    out = []
    for c in a.channels:
        body = array("d", c[0:e])
        for i in range(xf):
            t = i / xf
            # equal-power for uncorrelated material, closer to linear when correlated
            g_in = math.sin(t * math.pi / 2)
            g_out = math.cos(t * math.pi / 2)
            body[i] = c[i] * g_in + c[e + i] * g_out
        out.append(body)
    looped = Audio(out, rate)
    # How alike are the two passages being blended? Alike => the crossfade is inaudible.
    hop = max(1, int(0.01 * rate))
    env_a = [rms(m, s, s + hop) for s in range(0, xf - hop, hop)]
    env_b = [rms(m, e + s, e + s + hop) for s in range(0, xf - hop, hop)]
    env_corr = _pearson(env_a, env_b)
    level_diff = abs(db(rms(m, 0, xf) + 1e-7) - db(rms(m, e, e + xf) + 1e-7))
    match = max(0.0, min(100.0, 100 * (0.55 + 0.45 * env_corr) - 12 * max(0.0, level_diff - 1.5)))
    # By construction the wrap (last sample -> first sample) equals the source's own
    # continuation x[e-1] -> x[e], so only the blended head can be audible.
    info = {"loop_seconds": round(e / rate, 4), "crossfade_seconds": round(xf / rate, 3),
            "bars": bars, "bpm_grid": bpm if bars else None, "phase_correlation": round(best_c, 3),
            "crossfade_match": {"envelope_correlation": round(env_corr, 3), "level_difference_db": round(level_diff, 2),
                                "score": round(match, 1)}}
    info["seam"] = {"score": round(match, 1), "wrap": "continuous by construction (source continuation)",
                    "method": "bar-aligned equal-power crossfade" if bars else "phase-matched equal-power crossfade"}
    return looped, info


def _pearson(x: list[float], y: list[float]) -> float:
    n = min(len(x), len(y))
    if n < 3:
        return 0.0
    mx = sum(x[:n]) / n
    my = sum(y[:n]) / n
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((x[i] - mx) ** 2 for i in range(n))
    syy = sum((y[i] - my) ** 2 for i in range(n))
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else (1.0 if sxx == syy == 0 else 0.0)


# --------------------------------------------------------------- dither
def quantize_pcm16(a: Audio, seed: int = 1234, dither: bool = True) -> list[array]:
    rnd = random.Random(seed)
    lsb = 1.0 / 32768
    out = []
    for c in a.channels:
        q = array("h", bytes(2 * len(c)))
        for i, v in enumerate(c):
            if dither:
                v += (rnd.random() - rnd.random()) * lsb  # TPDF, ±1 LSB
            s = int(round(v * 32767))
            q[i] = 32767 if s > 32767 else (-32768 if s < -32768 else s)
        out.append(q)
    return out
