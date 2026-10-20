# Licensing

| Component | License | Included here? |
|---|---|---|
| This plugin's code, skill, demo and example workflow | MIT (see `LICENSE`) | Yes |
| Agent Audio (integration runtime) | MIT | No — installed separately |
| Stable Audio 3 Medium weights (via Agent Audio) | Stability AI Community License; T5Gemma parts under Gemma terms | No |
| Stable Audio Open 1.0 (ComfyUI SFX workflow) | Stability AI Community License (gated on Hugging Face) | No |
| ACE-Step 1.5 (ComfyUI music workflow) | MIT (per its model card) | No |
| CLAP scorer model `laion/larger_clap_general` | Apache-2.0 | No — downloaded by `setup_clap` |
| PyTorch / Transformers (CLAP environment only) | BSD-style / Apache-2.0 | No |
| ComfyUI, Hermes Agent, Godot, ffmpeg | their own licenses | No |

The plugin's MIT license grants no rights to any model or to what a model
produces. Before using generated audio commercially, read the terms of the
model that produced it. The Stability AI Community License has revenue-based
conditions; ACE-Step 1.5's MIT license is permissive. Check whether your use
is covered. You accept model terms yourself (on Hugging Face or in Agent
Audio's installer); this plugin never does it for you. Every result records
which backend produced the file, and manifests in plugin-data keep that
history.

Music prompts describe musical characteristics. Don't ask any model to
imitate a specific living artist or a copyrighted recording.

Sounds and music from the built-in procedural synthesizer come from this
plugin's MIT code and carry no model terms.

This page is a summary, not legal advice.
