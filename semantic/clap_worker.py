"""CLAP text-audio similarity worker (runs in its OWN Python environment).

Requirements in that environment: torch, transformers, numpy.
Protocol: one JSON object on stdin, one JSON object on stdout.

    {"model": "laion/larger_clap_general", "texts": ["prompt", "contrast", ...],
     "files": ["a.wav", ...]}
 -> {"ok": true, "model": ..., "device": "cpu", "cosine": [[...per text...] per file],
     "prompt_probability": [... per file]}

``--warmup MODEL`` downloads/caches the model and exits (the only step that
needs network access; scoring runs with HF_HUB_OFFLINE=1).
"""

import json
import struct
import sys


def read_wav(path):
    import numpy as np

    raw = open(path, "rb").read()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError(f"not a WAV file: {path}")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack_from("<I", raw, pos + 4)[0]
        if cid == b"fmt ":
            fmt = raw[pos + 8:pos + 8 + size]
        elif cid == b"data":
            data = raw[pos + 8:pos + 8 + size]
            break
        pos += 8 + size + (size & 1)
    tag, ch, rate, _, _, bits = struct.unpack_from("<HHIIHH", fmt, 0)
    if tag == 0xFFFE:
        tag = struct.unpack_from("<H", fmt, 24)[0]
    if tag == 3:
        x = np.frombuffer(data, dtype="<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 16:
        x = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
    elif bits == 32:
        x = np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648
    elif bits == 24:
        b = np.frombuffer(data[: len(data) // 3 * 3], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        x = v.astype(np.float32) / 8388608
    else:
        x = (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128) / 128
    x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    if rate != 48000:
        n_out = int(round(len(x) * 48000 / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x).astype(np.float32)
    return x


def load(model_name):
    import torch
    from transformers import ClapModel, ClapProcessor

    torch.set_num_threads(max(1, min(8, torch.get_num_threads())))
    model = ClapModel.from_pretrained(model_name)
    model.eval()
    return model, ClapProcessor.from_pretrained(model_name)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--warmup":
        load(sys.argv[2])
        print(json.dumps({"ok": True, "model": sys.argv[2], "warmed_up": True}))
        return
    req = json.loads(sys.stdin.read())
    import numpy as np
    import torch

    model, proc = load(req["model"])
    audios = [read_wav(f) for f in req["files"]]
    with torch.no_grad():
        t_in = proc(text=req["texts"], return_tensors="pt", padding=True)
        t_emb = model.get_text_features(**t_in)
        try:
            a_in = proc(audio=audios, sampling_rate=48000, return_tensors="pt")
        except TypeError:  # transformers < 4.48
            a_in = proc(audios=audios, sampling_rate=48000, return_tensors="pt")
        a_emb = model.get_audio_features(**a_in)
        t_emb = torch.nn.functional.normalize(t_emb, dim=-1)
        a_emb = torch.nn.functional.normalize(a_emb, dim=-1)
        cos = (a_emb @ t_emb.T).cpu().numpy()
        scale = float(model.logit_scale_a.exp().item()) if hasattr(model, "logit_scale_a") else 100.0
        logits = cos * scale
        probs = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = probs / probs.sum(axis=1, keepdims=True)
    print(json.dumps({"ok": True, "model": req["model"], "device": "cpu",
                      "cosine": [[round(float(v), 4) for v in row] for row in cos],
                      "prompt_probability": [round(float(v), 4) for v in probs[:, 0]]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # report, don't traceback-spam the caller
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
