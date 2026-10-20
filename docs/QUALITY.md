# How the plugin gets quality and correctness

No generator gets every take right. The plugin's job is to (1) ask the model
precisely, (2) generate more than one take when it matters, (3) measure each
take objectively, (4) fix what can be fixed, (5) pick the best, and (6) tell
you plainly what was and wasn't verified.

## 1. Asking precisely

- **SFX** prompts are built as *source + action + character + style* and end
  with `isolated game sound effect, clean, high quality, no music, no voice`
  (voice/music words in your description switch those off). UI sounds use
  `close and dry, very short` in place of `high quality`; ambience replaces the
  SFX suffix with `continuous ambient soundscape, stable background bed, no
  sudden loud events, suitable for looping` (plus `no music`/`no voice`).
- **Music** prompts follow Stable Audio / ACE-Step tagging: genre, description,
  mood, `featuring <instruments>`, energy, `<N> BPM`, `in <key>`, structure,
  then the form: loops get `seamless loop, steady consistent energy throughout,
  no intro, no ending, no fade out`; stingers `short musical sting, clear
  decisive ending, final note rings out`; one-off tracks `clear intro and
  satisfying ending`. Then `video game soundtrack`, `instrumental, no vocals`
  (unless vocals are requested) and `high quality, well mixed, stereo`.
- **Negative prompts** (ComfyUI Stable Audio workflows) get per-kind defaults
  such as `music, speech, hiss, distortion, low quality, muffled` for SFX.
- **Hard controls** are used when the backend has them: ComfyUI + ACE-Step 1.5
  receives the exact `bpm`, `keyscale`, `timesignature`, `duration`, `seed` and
  lyrics (`[instrumental]` by default). Agent Audio (Stable Audio 3 Medium)
  only takes text and duration, so tempo and key are requested in the prompt
  and then *measured*; results say which was the case.

## 2. Several takes

`candidates` (default 2 for SFX, 1 for music) renders independent takes;
`min_score` + `max_attempts` keeps going until one passes a quality bar.
Non-chosen takes stay in `plugin-data/.../candidates/` (or go into the project
as `_altN` with `keep_alternates`).

## 3. Measuring every take (`analysis.py`)

| Measurement | Method |
|---|---|
| Integrated loudness, max short-term / momentary | ITU-R BS.1770-4 K-weighting + gating (verified: 997 Hz full-scale sine = -3.01 LUFS) |
| True peak | 4x windowed-sinc oversampling around the loudest peaks (estimate) |
| Clipping | runs of ≥3 samples at full scale |
| DC offset, stereo phase (correlation) | direct |
| Leading / trailing silence, usable length, silent ratio | -55/-60 dBFS gates |
| Abrupt ending | last 10 ms vs loudest 10 ms |
| Attack time (SFX) | 5% → 90% of peak |
| Spectrum | centroid, 85% roll-off, flatness, low/mid/high balance |
| Tempo | onset-envelope autocorrelation with a 120 BPM prior; half/double-time aware |
| Loop seam | for built loops: similarity of the two crossfaded passages; for existing files: wrap discontinuity vs. the file's own internal discontinuities (drops only for music, so downbeats aren't penalised) |

Each problem becomes an issue with a severity, a message, and whether
mastering can fix it. The score is 100 minus penalties; ranking discounts
fixable issues because they will be fixed.

## 4. Fixing what can be fixed (`mastering.py`)

DC removal → optional mono → silence trim (head always, tail for one-shots) →
loop building (bar-aligned when BPM is known, end point chosen by waveform
correlation, equal-power crossfade, wrap continuous by construction) →
loudness or peak normalisation per kind → lookahead limiter if the true peak
would exceed the ceiling → edge fades (never on loops) → 16-bit TPDF dither →
WAV with an embedded `smpl` loop for loops (Godot's importer detects it;
verified with Godot 4.3) or Ogg Vorbis via ffmpeg (with `LOOPSTART`/`LOOPLENGTH`
tags). Looped WAVs carry one guard frame (a copy of frame 0) so engines that
treat the loop end as exclusive (Godot) and samplers that treat it as
inclusive (the RIFF spec) both loop without a jump.

Default targets (adjust per call with `post_process`):

| kind | level | ceiling |
|---|---|---|
| sfx | peak -1 dBTP | -1 dBTP |
| ui | -20 LUFS | -3 dBTP |
| stinger, music | -16 LUFS | -1 dBTP |
| ambience | -24 LUFS | -3 dBTP |

These are asset-level starting points; final balance belongs in the engine's
mixer (buses named `SFX`, `Music`, `Ambience` are used automatically in Godot
when they exist).

## 5. Does it match the description? (optional CLAP)

Signal metrics can't tell a sword hit from a door slam. With the optional CLAP
scorer (`laion/larger_clap_general`, Apache-2.0, runs locally in its own
environment) each take is also scored for text–audio similarity, and the final
rank is `0.55 × technical + 0.45 × semantic`. In this repository's tests the
real model scores a coin chime 0.37 vs. rain −0.09 for "a bright coin pickup
chime", and the reverse for "rain falling ambience".

Install: `scripts/setup_clap.ps1` (Windows) or `scripts/setup_clap.sh`. It
creates a separate venv (torch ≥ 2.6, transformers < 5), downloads the model
once (~1.5 GB total), and sets `clap_python`. Scoring then runs offline.

## 6. Honest reporting

Every result includes `quality` (score, verdict, remaining issues, issues the
mastering fixed, all metrics), `mastering` steps, the candidate table, the
backend's real capabilities, and `notes` such as "Agent Audio cannot force a
tempo; 128 BPM was requested in the prompt and ~127 BPM was measured".

## Limits

- Objective metrics and CLAP do not measure musical taste or how well a sound
  fits *your* game. Listen to the chosen take; alternates are kept.
- Tempo detection can report half- or double-time; that's reported as a
  relation, not a failure.
- True peak is an estimate (it checks the loudest peaks, not every sample).
- A loop that's seamless technically can still feel repetitive musically;
  longer loops (16+ bars) help.
