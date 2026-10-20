# Using ComfyUI as the generation backend

ComfyUI gives the plugin real quality controls: seeds, sampler steps/CFG,
negative prompts (Stable Audio Open), and, with **ACE-Step 1.5**, exact BPM,
key, time signature, duration and lyrics. It's the recommended music backend
if you have a GPU.

The plugin only talks to ComfyUI on this computer (`http://127.0.0.1:8188` by
default) and never edits your ComfyUI install.

## 1. Make a workflow that works in ComfyUI

- **Music: ACE-Step 1.5.** Start from ComfyUI's ACE-Step 1.5 template in the
  template browser (or the ACE-Step docs), download the models it asks for,
  and run it once. ACE-Step 1.5 is MIT licensed.
- **Sound effects: Stable Audio Open 1.0.** `stable_audio_open_api.json` here is
  the standard graph (checkpoint `stable-audio-open-1.0.safetensors` +
  `t5_base.safetensors` via CLIPLoader type `stable_audio`, 50 steps,
  dpmpp_3m_sde_gpu / exponential, CFG ≈ 5). The model is gated on Hugging Face
  under the Stability AI Community License; accept it there yourself.
  Load the JSON in ComfyUI to check that your file names match, then re-export it.

## 2. Export it in API format

*Workflow → Export (API)*. The normal "Save" format won't work; the plugin
tells you if you pick the wrong one.

## 3. Point the plugin at it

```bash
hermes config set plugins.entries.hermes-local-game-audio.settings.comfyui_workflow_music "C:/ComfyUI/workflows/ace_step_api.json"
hermes config set plugins.entries.hermes-local-game-audio.settings.comfyui_workflow_sfx   "C:/ComfyUI/workflows/stable_audio_open_api.json"
```

With `backend: auto`, a configured workflow is used for its kind (music
workflow → music/stingers/ambience, SFX workflow → sound effects), and Agent
Audio covers any kind without a workflow (if Agent Audio isn't installed, the
configured workflow is used for every kind). Force it per kind with `backend` /
`music_backend`.

Check with `/game-audio deep`: it contacts ComfyUI, lists GPU/CPU devices,
and confirms every node type in your workflows is installed.

## What the plugin changes in your workflow per request

It never needs placeholders; it finds nodes by type:

| Node | Inputs set |
|---|---|
| `CLIPTextEncode` feeding the sampler's positive input | `text` = the built prompt |
| `CLIPTextEncode` feeding the negative input | `text` = per-kind negative prompt |
| `TextEncodeAceStepAudio1.5` | `tags`, `bpm`, `keyscale`, `timesignature`, `duration`, `seed`, `lyrics` |
| `TextEncodeAceStepAudio` (1.0) | `tags`, `lyrics` |
| `EmptyLatentAudio`, `EmptyAceStep*LatentAudio` | `seconds`, `batch_size = 1` |
| `ConditioningStableAudio` | `seconds_start = 0`, `seconds_total` |
| `KSampler` (`seed`), `KSamplerAdvanced` / `RandomNoise` / `SamplerCustom` (`noise_seed`) | seed (plus `steps`/`cfg` on KSampler/KSamplerAdvanced if requested) |
| `SaveAudio` (and MP3/Opus/Advanced/Preview) | `filename_prefix = hlga/<id>` |

ComfyUI saves FLAC; the plugin decodes it with ffmpeg if present, otherwise
with its built-in FLAC decoder (bit-exact against ffmpeg in the tests).
