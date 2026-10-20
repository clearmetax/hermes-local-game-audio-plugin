"""Audio file conversion: ffmpeg when available, built-in FLAC decoder otherwise.

* decode_any(path) -> WAV bytes (float or PCM) for analysis/mastering;
* encode_ogg(wav_bytes, dest) -> Ogg Vorbis via ffmpeg (optional; WAV is always available).

The FLAC decoder is a straightforward implementation of the FLAC format
(STREAMINFO; CONSTANT / VERBATIM / FIXED / LPC subframes; Rice and Rice2
residuals with escapes; independent / left-side / side-right / mid-side
stereo). It exists so ComfyUI's FLAC output works on machines without ffmpeg.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from array import array
from pathlib import Path

from . import errors as E
from .safety import child_env
from .wav import _pack_pcm16

MAX_FLAC_BYTES = 80 * 1024 * 1024


def find_ffmpeg(configured: str = "") -> str | None:
    for raw in (configured, os.environ.get("FFMPEG_BINARY", "")):
        if raw and Path(raw).expanduser().is_file():
            return str(Path(raw).expanduser())
    return shutil.which("ffmpeg")


def _ffmpeg_to_wav(ffmpeg: str, path: Path) -> bytes:
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-f", "wav", "-acodec", "pcm_f32le", "pipe:1"],
        capture_output=True, timeout=300, check=False, env=child_env(),
    )
    if proc.returncode != 0 or not proc.stdout:
        raise E.AudioPluginError(E.INVALID_OUTPUT, f"ffmpeg could not decode {path.name}: {proc.stderr.decode(errors='replace')[-300:]}")
    data = bytearray(proc.stdout)
    # ffmpeg writes 0xFFFFFFFF sizes when piping; fix them by walking the real chunks.
    if data[4:8] == b"\xff\xff\xff\xff":
        data[4:8] = (len(data) - 8).to_bytes(4, "little")
    pos = 12
    while pos + 8 <= len(data):
        cid = bytes(data[pos:pos + 4])
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"data":
            if size == 0xFFFFFFFF or pos + 8 + size > len(data):
                data[pos + 4:pos + 8] = (len(data) - pos - 8).to_bytes(4, "little")
            break
        pos += 8 + size + (size & 1)
    return bytes(data)


def decode_any(path: Path, ffmpeg: str | None = None) -> bytes:
    path = Path(path)
    raw = path.read_bytes()
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return raw
    if ffmpeg:
        return _ffmpeg_to_wav(ffmpeg, path)
    if raw[:4] == b"fLaC":
        return flac_to_wav(raw)
    raise E.AudioPluginError(
        E.INVALID_OUTPUT,
        f"Cannot decode {path.suffix or 'this'} audio without ffmpeg",
        hint="Install ffmpeg (or set ffmpeg_executable), or have the backend save FLAC/WAV.",
    )


def encode_ogg(wav_bytes: bytes, dest: Path, ffmpeg: str, *, quality: int = 6, loop_frames: int | None = None) -> None:
    """Encode to Ogg Vorbis at ``dest`` (must not exist). Loop length goes in LOOPSTART/LOOPLENGTH tags."""
    if dest.exists():
        raise E.AudioPluginError(E.FILE_EXISTS, f"Refusing to overwrite existing file: {dest}")
    with tempfile.TemporaryDirectory(prefix=".hlga-ogg-", dir=dest.parent) as tmp:
        src = Path(tmp) / "in.wav"
        out = Path(tmp) / "out.ogg"
        src.write_bytes(wav_bytes)
        cmd = [ffmpeg, "-v", "error", "-nostdin", "-i", str(src), "-c:a", "libvorbis", "-q:a", str(int(quality))]
        if loop_frames:
            cmd += ["-metadata", "LOOPSTART=0", "-metadata", f"LOOPLENGTH={loop_frames}"]
        cmd.append(str(out))
        proc = subprocess.run(cmd, capture_output=True, timeout=300, check=False, env=child_env())  # noqa: S603
        if proc.returncode != 0 or not out.is_file():
            raise E.AudioPluginError(E.BACKEND_ERROR, f"ffmpeg Ogg encode failed: {proc.stderr.decode(errors='replace')[-300:]}")
        try:
            os.link(out, dest)
        except FileExistsError:
            raise E.AudioPluginError(E.FILE_EXISTS, f"Refusing to overwrite existing file: {dest}") from None
        except OSError:
            with open(dest, "xb") as fh:
                fh.write(out.read_bytes())


# ------------------------------------------------------------------ FLAC
class _Bits:
    __slots__ = ("s", "p")

    def __init__(self, data: bytes, start: int):
        self.s = bin(int.from_bytes(b"\x01" + data, "big"))[3:]  # leading 1 keeps zeros
        self.p = start * 8

    def u(self, n: int) -> int:
        if n == 0:
            return 0
        p = self.p
        self.p = p + n
        return int(self.s[p:p + n], 2)

    def s_(self, n: int) -> int:
        v = self.u(n)
        return v - (1 << n) if n and v >> (n - 1) else v

    def unary(self) -> int:
        q = self.s.find("1", self.p)
        if q < 0:
            raise ValueError("truncated FLAC stream")
        n = q - self.p
        self.p = q + 1
        return n

    def align(self) -> None:
        self.p = (self.p + 7) & ~7


def _residual(b: _Bits, block: int, order: int, out: list[int]) -> None:
    method = b.u(2)
    if method > 1:
        raise ValueError("reserved residual coding method")
    pbits, esc = (4, 15) if method == 0 else (5, 31)
    porder = b.u(4)
    parts = 1 << porder
    s = b.s
    for part in range(parts):
        n = (block >> porder) - (order if part == 0 else 0)
        k = b.u(pbits)
        if k == esc:
            raw = b.u(5)
            for _ in range(n):
                out.append(b.s_(raw))
            continue
        p = b.p
        find = s.find
        for _ in range(n):
            q = find("1", p)
            v = (q - p) << k
            p = q + 1
            if k:
                v |= int(s[p:p + k], 2)
                p += k
            out.append((v >> 1) ^ -(v & 1))
        b.p = p


def _subframe(b: _Bits, block: int, bps: int) -> list[int]:
    if b.u(1):
        raise ValueError("bad subframe padding")
    t = b.u(6)
    wasted = 0
    if b.u(1):
        wasted = b.unary() + 1
        bps -= wasted
    if t == 0:
        v = b.s_(bps)
        samples = [v] * block
    elif t == 1:
        samples = [b.s_(bps) for _ in range(block)]
    elif 8 <= t <= 12:
        order = t - 8
        samples = [b.s_(bps) for _ in range(order)]
        res: list[int] = []
        _residual(b, block, order, res)
        app = samples.append
        if order == 0:
            samples = res
        elif order == 1:
            for r in res:
                app(r + samples[-1])
        elif order == 2:
            for r in res:
                app(r + 2 * samples[-1] - samples[-2])
        elif order == 3:
            for r in res:
                app(r + 3 * samples[-1] - 3 * samples[-2] + samples[-3])
        else:
            for r in res:
                app(r + 4 * samples[-1] - 6 * samples[-2] + 4 * samples[-3] - samples[-4])
    elif t >= 32:
        order = (t & 31) + 1
        samples = [b.s_(bps) for _ in range(order)]
        prec = b.u(4) + 1
        if prec == 16:
            raise ValueError("invalid LPC precision")
        shift = b.s_(5)
        coefs = [b.s_(prec) for _ in range(order)]
        res = []
        _residual(b, block, order, res)
        rc = coefs[::-1]  # rc[j] multiplies samples[i - order + j]
        app = samples.append
        i = order
        for r in res:
            acc = 0
            base = i - order
            for j in range(order):
                acc += rc[j] * samples[base + j]
            app(r + (acc >> shift))
            i += 1
    else:
        raise ValueError(f"reserved subframe type {t}")
    if wasted:
        samples = [v << wasted for v in samples]
    return samples


def flac_to_wav(raw: bytes) -> bytes:
    if len(raw) > MAX_FLAC_BYTES:
        raise E.AudioPluginError(E.INVALID_OUTPUT, "FLAC too large for the built-in decoder; install ffmpeg")
    if raw[:4] != b"fLaC":
        raise E.AudioPluginError(E.INVALID_OUTPUT, "Not a FLAC file")
    pos = 4
    rate = channels = bps = 0
    total = 0
    while True:
        hdr = raw[pos]
        length = int.from_bytes(raw[pos + 1:pos + 4], "big")
        if hdr & 0x7F == 0:
            si = int.from_bytes(raw[pos + 4 + 10:pos + 4 + 18], "big")
            rate = si >> 44
            channels = ((si >> 41) & 7) + 1
            bps = ((si >> 36) & 31) + 1
            total = si & ((1 << 36) - 1)
        pos += 4 + length
        if hdr & 0x80:
            break
    try:
        b = _Bits(raw, pos)
        chans: list[list[int]] = [[] for _ in range(channels)]
        nbits = len(raw) * 8
        while b.p + 16 <= nbits:
            if b.u(14) != 0x3FFE:
                raise ValueError("lost FLAC frame sync")
            b.u(2)
            bs_code, sr_code = b.u(4), b.u(4)
            ch_assign, ss_code = b.u(4), b.u(3)
            b.u(1)
            first = b.u(8)  # UTF-8-like coded frame number
            extra = 0
            m = 0x80
            while first & m:
                extra += 1
                m >>= 1
            for _ in range(max(0, extra - 1)):
                b.u(8)
            if bs_code == 1:
                block = 192
            elif 2 <= bs_code <= 5:
                block = 576 << (bs_code - 2)
            elif bs_code == 6:
                block = b.u(8) + 1
            elif bs_code == 7:
                block = b.u(16) + 1
            else:
                block = 256 << (bs_code - 8)
            if sr_code == 12:
                b.u(8)
            elif sr_code in (13, 14):
                b.u(16)
            b.u(8)  # CRC-8
            fbps = {0: bps, 1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}.get(ss_code, bps)
            if ch_assign < 8:
                subs = [_subframe(b, block, fbps) for _ in range(ch_assign + 1)]
            else:
                side_first = ch_assign == 9
                a0 = _subframe(b, block, fbps + (1 if side_first else 0))
                a1 = _subframe(b, block, fbps + (0 if side_first else 1))
                if ch_assign == 8:  # left/side
                    subs = [a0, [x - y for x, y in zip(a0, a1)]]
                elif ch_assign == 9:  # side/right
                    subs = [[x + y for x, y in zip(a0, a1)], a1]
                else:  # mid/side
                    left, right = [], []
                    for mid, side in zip(a0, a1):
                        mid = (mid << 1) | (side & 1)
                        left.append((mid + side) >> 1)
                        right.append((mid - side) >> 1)
                    subs = [left, right]
            b.align()
            b.u(16)  # CRC-16
            for c in range(channels):
                chans[c].extend(subs[c])
            if total and len(chans[0]) >= total:
                break
    except (ValueError, IndexError) as exc:
        raise E.AudioPluginError(E.INVALID_OUTPUT, f"FLAC decode failed: {exc}") from exc
    if bps == 16:
        return _pack_pcm16([array("h", c) for c in chans], rate)
    scale = 1.0 / (1 << (bps - 1))
    return _pack_float32([array("f", (v * scale for v in c)) for c in chans], rate)


def _pack_float32(chans: list[array], rate: int) -> bytes:
    import struct
    import sys

    n, ch = len(chans[0]), len(chans)
    inter = array("f", bytes(4 * n * ch))
    for c, data in enumerate(chans):
        inter[c::ch] = data
    if sys.byteorder == "big":  # pragma: no cover
        inter.byteswap()
    payload = inter.tobytes()
    fmt = struct.pack("<IHHIIHH", 16, 3, ch, rate, rate * ch * 4, ch * 4, 32)
    body = b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", len(body)) + body
