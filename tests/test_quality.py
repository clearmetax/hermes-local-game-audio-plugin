"""DSP, analysis, mastering and codec correctness against known references."""

import math
import random
import shutil
import subprocess
from array import array

import pytest
from hlga_under_test import analysis, codecs_io, dsp, mastering, wav
from hlga_under_test.audio_backend.procedural_music import synth_music


def sine(f, amp, secs, rate=48000, phase=0.0):
    return array("d", (amp * math.sin(2 * math.pi * f * i / rate + phase) for i in range(int(secs * rate))))


# ------------------------------------------------------------- loudness
@pytest.mark.parametrize("rate", [44100, 48000])
def test_bs1770_reference_tone(rate):
    # BS.1770: a 997 Hz full-scale sine in one channel reads -3.01 LUFS.
    assert abs(dsp.integrated_loudness(dsp.Audio([sine(997, 1.0, 5, rate)], rate)) - (-3.01)) < 0.05


def test_stereo_minus20_dbfs_reads_minus20_lufs_including_long_file_path():
    a = dsp.Audio([sine(997, 0.1, 25), sine(997, 0.1, 25)], 48000)  # >20 s uses the decimated path
    assert abs(dsp.integrated_loudness(a) - (-20.0)) < 0.05


def test_gating_ignores_silence():
    tone = sine(997, 0.1, 5)
    padded = array("d", [0.0]) * 48000 * 10 + tone
    a, b = dsp.Audio([tone, tone], 48000), dsp.Audio([padded, padded], 48000)
    assert abs(dsp.integrated_loudness(a) - dsp.integrated_loudness(b)) < 0.2


def test_true_peak_detects_intersample_overs():
    a = dsp.Audio([sine(12000, 1.0, 0.5, 48000, math.pi / 4)], 48000)  # samples at ±0.707
    assert dsp.peak(a) < 0.75
    assert 0.95 < dsp.true_peak(a) < 1.08


def test_limiter_never_exceeds_ceiling_and_is_smooth():
    rnd = random.Random(1)
    x = array("d", (1.8 * math.sin(i * 0.03) * (1 if (i // 9000) % 2 else 0.3) + 0.05 * rnd.uniform(-1, 1) for i in range(96000)))
    y, gr = dsp.limit(dsp.Audio([x], 48000), 0.89)
    assert dsp.peak(y) <= 0.89 + 1e-9 and gr < -5
    # no click: sample-to-sample change stays bounded like the input's
    dy = max(abs(y.channels[0][i] - y.channels[0][i - 1]) for i in range(1, y.frames))
    dx = max(abs(x[i] - x[i - 1]) for i in range(1, len(x)))
    assert dy <= dx * 1.05


# ------------------------------------------------------------- rhythm
@pytest.mark.parametrize("bpm", [90, 128, 150])
def test_tempo_estimate_on_click_track(bpm):
    rate = 44100
    n = rate * 12
    c = array("d", [0.0]) * n
    for b in range(int(12 * bpm / 60)):
        s = int(b * 60 / bpm * rate)
        for k in range(300):
            if s + k < n:
                c[s + k] = math.sin(2 * math.pi * 1500 * k / rate) * math.exp(-k / 60)
    est, conf = dsp.estimate_tempo(dsp.Audio([c], rate))
    assert dsp.bpm_matches(est, bpm) and conf > 0.3


def test_procedural_music_tempo_is_measurable():
    a = dsp.Audio(synth_music("upbeat chiptune", 12, 3, {"bpm": 128}, 44100), 44100)
    est, _ = dsp.estimate_tempo(a)
    assert dsp.bpm_matches(est, 128)


# ------------------------------------------------------------- loops
def test_bar_aligned_loop_has_exact_length_and_good_seam():
    a = dsp.Audio(synth_music("calm ambient menu", 14, 5, {"bpm": 120, "key": "C"}, 44100), 44100)
    looped, info = dsp.make_loop(a, loop_seconds=8.0, crossfade_s=0.5, bpm=120)
    assert info["bars"] == 4 and abs(looped.seconds - 8.0) < 0.02
    assert info["seam"]["score"] >= 70


def test_seam_metric_flags_a_naive_cut():
    rnd = random.Random(2)
    x = array("d")
    lp = 0.0
    for i in range(44100 * 6):  # smooth noise bed with a slow swell
        lp += 0.05 * (rnd.uniform(-1, 1) - lp)
        x.append(lp * (0.3 + 0.7 * (i / (44100 * 6))))
    naive = dsp.Audio([x], 44100)
    assert dsp.loop_seam(naive)["score"] < 50  # end is much louder than the start
    looped, info = dsp.make_loop(naive, crossfade_s=1.0)
    assert dsp.loop_seam(looped)["score"] > dsp.loop_seam(naive)["score"]


# ------------------------------------------------------------- analysis
def test_analysis_catches_clipping_late_start_and_abrupt_end():
    x = array("d", [0.0]) * 12000 + array("d", (max(-1, min(1, 3 * math.sin(i * 0.05))) for i in range(20000)))
    rep = analysis.analyze(dsp.Audio([x], 48000), kind="sfx")
    codes = {i["code"] for i in rep["issues"]}
    assert {"clipping", "late_start", "abrupt_end"} <= codes
    assert rep["verdict"] != "good"


def test_analysis_catches_phase_problems():
    t = sine(300, 0.5, 2)
    inv = array("d", (-v for v in t))
    rep = analysis.analyze(dsp.Audio([t, inv], 48000), kind="music")
    assert "phase" in {i["code"] for i in rep["issues"]}


def test_clean_sfx_scores_well():
    rate = 48000
    x = array("d", (0.6 * math.sin(2 * math.pi * 180 * i / rate) * math.exp(-i / (rate * 0.08)) for i in range(int(0.5 * rate))))
    rep = analysis.analyze(dsp.Audio([x], rate), kind="sfx")
    assert rep["verdict"] == "good", rep["issues"]


# ------------------------------------------------------------- mastering
@pytest.mark.parametrize("kind,target", [("music", -16.0), ("ambience", -24.0), ("ui", -20.0)])
def test_mastering_hits_loudness_targets_under_ceiling(kind, target):
    a = dsp.Audio(synth_music("battle music", 8, 9, {"bpm": 140}, 44100), 44100)
    out, steps, _ = mastering.master(a, mastering.MasterOptions(kind=kind))
    assert abs(dsp.integrated_loudness(out) - target) < 0.6
    ceiling = mastering.PROFILES[kind]["ceiling_dbtp"]
    assert dsp.db(dsp.true_peak(out)) <= ceiling + 0.15


def test_loud_input_is_limited_not_clipped():
    a = dsp.Audio([sine(997, 0.5, 4, 44100)], 44100)  # ~ -9 LUFS; +6 dB gain would peak at 0 dBFS
    out, steps, _ = mastering.master(a, mastering.MasterOptions(kind="music", target_lufs=-3.0))
    assert dsp.db(dsp.true_peak(out)) <= -0.85
    assert any("limited" in s for s in steps)


def test_loops_are_never_faded():
    a = dsp.Audio(synth_music("chiptune", 10, 1, {"bpm": 120}, 44100), 44100)
    out, steps, info = mastering.master(a, mastering.MasterOptions(kind="music", loop=True, bpm=120))
    assert not any("fades" in s for s in steps) and info["bars"]
    assert dsp.rms(dsp.mono(out), 0, 2000) > 0.01  # head not faded in


def test_mastering_disabled_returns_input():
    a = dsp.Audio([sine(440, 0.2, 1)], 48000)
    out, steps, _ = mastering.master(a, mastering.MasterOptions(enabled=False))
    assert out is a and "disabled" in steps[0]


# ------------------------------------------------------------- codecs
def _flac_fixture(tmp_path):
    import fake_comfyui

    rnd = random.Random(3)
    left = [int(9000 * math.sin(i * 0.02)) + rnd.randint(-50, 50) for i in range(20000)]
    right = [int(v * 0.5) for v in left]
    return fake_comfyui.flac_encode([left, right], 44100), left, right


def test_builtin_flac_decoder_verbatim(tmp_path):
    data, left, right = _flac_fixture(tmp_path)
    a = wav.decode(codecs_io.flac_to_wav(data))
    assert a.frames == 20000 and a.n_channels == 2
    assert [round(v * 32768) for v in a.channels[0][:500]] == left[:500]
    assert [round(v * 32768) for v in a.channels[1][-500:]] == right[-500:]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("args", [["-sample_fmt", "s16"], ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"], ["-ac", "1"],
                                  ["-compression_level", "12", "-lpc_type", "cholesky"]])
def test_builtin_flac_decoder_matches_ffmpeg(tmp_path, args):
    src = tmp_path / "m.wav"
    src.write_bytes(wav.encode_pcm16(synth_music("chiptune", 4, 3, {"bpm": 120}, 44100), 44100))
    f = tmp_path / "m.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), *args, str(f)], check=True)
    mine = wav.decode(codecs_io.flac_to_wav(f.read_bytes()))
    ref = wav.decode(codecs_io._ffmpeg_to_wav("ffmpeg", f))
    assert mine.frames == ref.frames
    assert max(max(abs(x - y) for x, y in zip(c1, c2)) for c1, c2 in zip(mine.channels, ref.channels)) < 1e-6


def test_decode_any_rejects_unknown_without_ffmpeg(tmp_path):
    p = tmp_path / "x.ogg"
    p.write_bytes(b"OggS" + b"\0" * 100)
    from hlga_under_test import errors as E

    with pytest.raises(E.AudioPluginError):
        codecs_io.decode_any(p, None)
