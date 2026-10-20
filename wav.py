"""Dependency-free WAV validation, inspection and light post-processing.

Python's ``wave`` module only reads integer PCM, while Stable Audio runtimes
may emit 32-bit float WAVs, so this module parses RIFF chunks directly.
Post-processed output is always 16-bit PCM, which every engine imports.
"""

from __future__ import annotations

import math
import struct
from array import array
from dataclasses import asdict, dataclass
from pathlib import Path

from . import errors as E

MAX_WAV_BYTES = 200 * 1024 * 1024
_PCM = 1
_FLOAT = 3
_EXTENSIBLE = 0xFFFE


@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    encoding: str  # "pcm" | "float"
    frames: int
    duration_seconds: float
    peak_dbfs: float | None
    size_bytes: int

    def to_dict(self) -> dict:
        d = asdict(self)
        d["duration_seconds"] = round(self.duration_seconds, 3)
        if self.peak_dbfs is not None:
            d["peak_dbfs"] = round(self.peak_dbfs, 1)
        return d


@dataclass
class _Parsed:
    info: WavInfo
    data: bytes
    block_align: int


def _parse(raw: bytes) -> _Parsed:
    if len(raw) > MAX_WAV_BYTES:
        raise E.AudioPluginError(E.INVALID_OUTPUT, f"WAV is larger than {MAX_WAV_BYTES // (1024 * 1024)} MB")
    if len(raw) < 44 or raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise E.AudioPluginError(E.INVALID_OUTPUT, "Not a RIFF/WAVE file")
    pos = 12
    fmt = None
    data = None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        body_start = pos + 8
        body_end = body_start + size
        if cid == b"fmt ":
            if size < 16 or body_end > len(raw):
                raise E.AudioPluginError(E.INVALID_OUTPUT, "Malformed fmt chunk")
            fmt = raw[body_start:body_end]
        elif cid == b"data":
            if body_end > len(raw):
                raise E.AudioPluginError(E.INVALID_OUTPUT, "WAV data is truncated")
            data = raw[body_start:body_end]
            break
        pos = body_end + (size & 1)
    if fmt is None or data is None:
        raise E.AudioPluginError(E.INVALID_OUTPUT, "WAV is missing fmt or data chunk")
    tag, channels, rate, _byte_rate, block_align, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag == _EXTENSIBLE and len(fmt) >= 40:
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if tag == _PCM and bits in (8, 16, 24, 32):
        encoding = "pcm"
    elif tag == _FLOAT and bits in (32, 64):
        encoding = "float"
    else:
        raise E.AudioPluginError(E.INVALID_OUTPUT, f"Unsupported WAV encoding (format {tag}, {bits}-bit)")
    if not (1 <= channels <= 8) or not (8000 <= rate <= 192000):
        raise E.AudioPluginError(E.INVALID_OUTPUT, f"Implausible WAV format: {channels} ch @ {rate} Hz")
    if block_align != channels * bits // 8:
        raise E.AudioPluginError(E.INVALID_OUTPUT, "WAV block alignment is inconsistent")
    frames = len(data) // block_align
    if frames == 0:
        raise E.AudioPluginError(E.INVALID_OUTPUT, "WAV contains no audio frames")
    info = WavInfo(rate, channels, bits, encoding, frames, frames / rate, None, len(raw))
    return _Parsed(info, data[: frames * block_align], block_align)


def _decode(p: _Parsed) -> list[array]:
    """Return per-channel float arrays in [-1, 1]."""
    info = p.info
    bits = info.bits_per_sample
    data = p.data
    if info.encoding == "float":
        flat = array("f" if bits == 32 else "d")
        flat.frombytes(data)
        if flat.itemsize * 8 != bits:  # pragma: no cover - exotic platforms
            raise E.AudioPluginError(E.INVALID_OUTPUT, "Platform float size mismatch")
        samples = [0.0 if not math.isfinite(v) else v for v in flat]
    elif bits == 16:
        flat = array("h")
        flat.frombytes(data)
        samples = [v / 32768.0 for v in flat]
    elif bits == 32:
        flat = array("i")
        flat.frombytes(data)
        samples = [v / 2147483648.0 for v in flat]
    elif bits == 8:
        samples = [(b - 128) / 128.0 for b in data]
    else:  # 24-bit
        samples = []
        for i in range(0, len(data), 3):
            v = data[i] | (data[i + 1] << 8) | (data[i + 2] << 16)
            if v & 0x800000:
                v -= 0x1000000
            samples.append(v / 8388608.0)
    ch = info.channels
    return [array("d", samples[c::ch]) for c in range(ch)]


def _peak_dbfs(channels: list[array]) -> float | None:
    peak = 0.0
    for c in channels:
        if c:
            peak = max(peak, max(c), -min(c))
    return None if peak <= 0 else 20 * math.log10(peak)


def inspect_bytes(raw: bytes, *, measure_peak: bool = True) -> WavInfo:
    parsed = _parse(raw)
    if measure_peak and parsed.info.frames * parsed.info.channels <= 20_000_000:
        parsed.info.peak_dbfs = _peak_dbfs(_decode(parsed))
    return parsed.info


def inspect_file(path: Path, *, measure_peak: bool = True) -> WavInfo:
    return inspect_bytes(Path(path).read_bytes(), measure_peak=measure_peak)


def validate(raw: bytes, *, expected_seconds: float | None = None, max_seconds: float = 400.0) -> WavInfo:
    info = inspect_bytes(raw)
    if info.duration_seconds > max_seconds + 1:
        raise E.AudioPluginError(E.INVALID_OUTPUT, f"WAV is {info.duration_seconds:.1f}s, over the {max_seconds}s limit")
    if expected_seconds and info.duration_seconds < max(0.05, expected_seconds * 0.25):
        raise E.AudioPluginError(E.INVALID_OUTPUT, "WAV is much shorter than requested")
    if info.peak_dbfs is None:
        raise E.AudioPluginError(E.INVALID_OUTPUT, "WAV is completely silent")
    return info


def decode(raw: bytes):
    """Parse WAV bytes into a dsp.Audio (float samples per channel)."""
    from .dsp import Audio

    parsed = _parse(raw)
    return Audio(_decode(parsed), parsed.info.sample_rate)


def read_loop(raw: bytes) -> tuple[int, int] | None:
    """Return (start, end) frames from a RIFF ``smpl`` loop, if present."""
    pos = 12
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        if cid == b"smpl" and size >= 36 + 24:
            count = struct.unpack_from("<I", raw, pos + 8 + 28)[0]
            if count:
                start, end = struct.unpack_from("<II", raw, pos + 8 + 36 + 8)
                return start, end
        pos += 8 + size + (size & 1)
    return None


def encode(audio, *, loop: bool = False, dither: bool = True) -> bytes:
    """16-bit PCM WAV with TPDF dither; ``loop`` embeds a whole-file ``smpl`` loop.

    Godot's WAV importer ("Loop Mode: Detect From WAV") and most samplers read
    the ``smpl`` chunk, so looping assets loop without extra import settings.
    """
    from .dsp import quantize_pcm16

    chans = quantize_pcm16(audio, dither=dither)
    if loop and audio.frames:
        # Guard frame = copy of frame 0. smpl end (inclusive, per spec) = N; engines that
        # treat the end as exclusive (Godot) loop over exactly frames 0..N-1.
        for c in chans:
            c.append(c[0])
        return _pack_pcm16(chans, audio.rate, loop_frames=audio.frames + 1)
    return _pack_pcm16(chans, audio.rate)


def _pack_pcm16(chans: list[array], rate: int, loop_frames: int | None = None) -> bytes:
    import sys

    n = len(chans[0]) if chans else 0
    ch = len(chans)
    out = array("h", bytes(2 * n * ch))
    for c, data in enumerate(chans):
        out[c::ch] = data
    if sys.byteorder == "big":  # pragma: no cover
        out.byteswap()
    payload = out.tobytes()
    body = b"WAVE"
    body += b"fmt " + struct.pack("<IHHIIHH", 16, _PCM, ch, rate, rate * ch * 2, ch * 2, 16)
    body += b"data" + struct.pack("<I", len(payload)) + payload
    if len(payload) & 1:
        body += b"\0"
    if loop_frames:
        period_ns = int(round(1e9 / rate))
        smpl = struct.pack("<IIIIIIIII", 0, 0, period_ns, 60, 0, 0, 0, 1, 0)
        smpl += struct.pack("<IIIIII", 0, 0, 0, max(0, loop_frames - 1), 0, 0)
        body += b"smpl" + struct.pack("<I", len(smpl)) + smpl
    return b"RIFF" + struct.pack("<I", len(body)) + body


def encode_pcm16(channels: list[array], rate: int) -> bytes:
    """Undithered 16-bit encode of float channels (used by the procedural synth)."""
    q = []
    for data in channels:
        a = array("h", bytes(2 * len(data)))
        for i, v in enumerate(data):
            v = -1.0 if v < -1.0 else (1.0 if v > 1.0 else v)
            a[i] = int(round(v * 32767))
        q.append(a)
    return _pack_pcm16(q, rate)
