import math
import struct

import pytest
from hlga_under_test import errors as E
from hlga_under_test import wav


def make_wav(samples, rate=44100, channels=1, bits=16, fmt=1):
    if fmt == 3:
        data = b"".join(struct.pack("<f", s) for s in samples)
    elif bits == 16:
        data = b"".join(struct.pack("<h", int(s * 32767)) for s in samples)
    elif bits == 24:
        data = b"".join(int(s * 8388607).to_bytes(3, "little", signed=True) for s in samples)
    else:
        raise ValueError
    block = channels * bits // 8
    fmt_chunk = struct.pack("<IHHIIHH", 16, fmt, channels, rate, rate * block, block, bits)
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + fmt_chunk + b"data" + struct.pack("<I", len(data)) + data


TONE = [0.5 * math.sin(i * 0.1) for i in range(4410)]


@pytest.mark.parametrize("bits,fmt", [(16, 1), (24, 1), (32, 3)])
def test_parse_formats(bits, fmt):
    info = wav.validate(make_wav(TONE, bits=bits, fmt=fmt))
    assert info.sample_rate == 44100 and info.channels == 1
    assert abs(info.duration_seconds - 0.1) < 1e-6
    assert -7 < info.peak_dbfs < -5


@pytest.mark.parametrize(
    "raw",
    [b"", b"hello world" * 10, b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 40],
)
def test_malformed(raw):
    with pytest.raises(E.AudioPluginError) as ei:
        wav.validate(raw)
    assert ei.value.code == E.INVALID_OUTPUT


def test_truncated_data_chunk():
    good = make_wav(TONE)
    with pytest.raises(E.AudioPluginError):
        wav.validate(good[: len(good) // 2])


def test_silent_rejected():
    with pytest.raises(E.AudioPluginError, match="silent"):
        wav.validate(make_wav([0.0] * 1000))


def test_too_long_rejected():
    with pytest.raises(E.AudioPluginError, match="limit"):
        wav.validate(make_wav(TONE * 30), max_seconds=1)


def test_encode_roundtrip_with_loop_chunk():
    a = wav.decode(make_wav(TONE))
    raw = wav.encode(a, loop=True)
    info = wav.validate(raw)
    assert info.bits_per_sample == 16 and info.frames == len(TONE) + 1  # + guard frame (= frame 0)
    assert wav.read_loop(raw) == (0, len(TONE))
    assert wav.read_loop(wav.encode(a)) is None
    back = wav.decode(raw)
    assert max(abs(x - y) for x, y in zip(back.channels[0], a.channels[0])) < 3 / 32768  # dither is ±1 LSB


def test_dither_is_deterministic_and_small():
    a = wav.decode(make_wav([0.0] * 100 + TONE))
    assert wav.encode(a) == wav.encode(a)
