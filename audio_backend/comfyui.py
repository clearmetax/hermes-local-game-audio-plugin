"""Local ComfyUI backend (Stable Audio Open, ACE-Step 1.0/1.5, or any audio workflow).

You export a working workflow from ComfyUI with *Workflow > Export (API)*. The
plugin patches it per request by node type (no placeholders needed):

* text      -> CLIPTextEncode.text (the encoder feeding the sampler's ``positive``),
               TextEncodeAceStepAudio / TextEncodeAceStepAudio1.5 ``tags``
* negative  -> CLIPTextEncode feeding the sampler's ``negative`` (when present)
* duration  -> EmptyLatentAudio / EmptyAceStep*LatentAudio ``seconds``,
               ConditioningStableAudio ``seconds_total``, ACE-Step 1.5 ``duration``
* music     -> ACE-Step 1.5 ``bpm``, ``keyscale``, ``timesignature``, ``lyrics``;
               ACE-Step 1.0 ``lyrics``
* seed      -> KSampler/KSamplerAdvanced/RandomNoise seeds, ACE-Step 1.5 ``seed``
* quality   -> optional ``steps`` / ``cfg`` overrides on the sampler
* output    -> Save/Preview audio node ``filename_prefix``; batch sizes forced to 1

Only the ComfyUI HTTP API on this computer is used (``/prompt``, ``/history``,
``/view``, ``/interrupt``, ``/object_info``, ``/system_stats``); remote hosts
are refused unless ``comfyui_allow_remote`` is enabled.
"""

from __future__ import annotations

import copy
import json
import random
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import errors as E
from .base import AudioBackend, BackendStatus, GenerationRequest, Session

TEXT_ENCODERS = {"CLIPTextEncode": "text", "TextEncodeAceStepAudio": "tags", "TextEncodeAceStepAudio1.5": "tags"}
LATENTS = ("EmptyLatentAudio", "EmptyAceStepLatentAudio", "EmptyAceStep1.5LatentAudio")
SAMPLERS = {"KSampler": "seed", "KSamplerAdvanced": "noise_seed", "RandomNoise": "noise_seed", "SamplerCustom": "noise_seed"}
OUTPUTS = ("SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "SaveAudioAdvanced", "PreviewAudio")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _link(v: Any) -> str | None:
    return str(v[0]) if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) else None


def _upstream_encoder(wf: dict, link: Any, depth: int = 0) -> str | None:
    """Follow a conditioning link upward until a text encoder is found.

    Nodes with paired outputs (ConditioningStableAudio: 0=positive, 1=negative)
    are followed through the input that matches the output index.
    """
    node_id = _link(link)
    if node_id is None or depth > 12 or node_id not in wf:
        return None
    node = wf[node_id]
    if node.get("class_type") in TEXT_ENCODERS:
        return node_id
    inputs = node.get("inputs", {})
    out_idx = link[1] if isinstance(link, list) else 0
    order = ["conditioning", "conditioning_1", "conditioning_to", "cond"]
    if "positive" in inputs and "negative" in inputs:
        order = (["negative"] if out_idx == 1 else ["positive"]) + order
    else:
        order = ["positive", "negative"] + order
    for key in order:
        if _link(inputs.get(key)):
            found = _upstream_encoder(wf, inputs[key], depth + 1)
            if found:
                return found
    return None


def describe_workflow(wf: dict) -> dict[str, Any]:
    if not isinstance(wf, dict) or not wf or not all(isinstance(v, dict) and "class_type" in v for v in wf.values()):
        raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, "Workflow is not in ComfyUI API format",
                                 hint="In ComfyUI use Workflow > Export (API), not the regular Save.")
    classes = {nid: n["class_type"] for nid, n in wf.items()}
    samplers = [nid for nid, c in classes.items() if c in ("KSampler", "KSamplerAdvanced", "SamplerCustom", "CFGGuider")]
    pos_nodes: list[str] = []
    neg_nodes: list[str] = []
    for s in sorted(samplers, key=lambda x: (len(x), x)):
        inp = wf[s].get("inputs", {})
        p_ = _upstream_encoder(wf, inp.get("positive"))
        n_ = _upstream_encoder(wf, inp.get("negative"))
        if p_ and p_ not in pos_nodes:
            pos_nodes.append(p_)
        if n_ and n_ not in neg_nodes and n_ not in pos_nodes:
            neg_nodes.append(n_)
    if not pos_nodes:
        enc = [nid for nid, c in classes.items() if c in TEXT_ENCODERS]
        if len(enc) == 1:
            pos_nodes = enc
    neg_nodes = [n for n in neg_nodes if n not in pos_nodes and classes.get(n) == "CLIPTextEncode"]
    pos = pos_nodes[0] if pos_nodes else None
    neg = neg_nodes[0] if neg_nodes else None
    outputs = [nid for nid, c in classes.items() if c in OUTPUTS]
    ace15 = [nid for nid, c in classes.items() if c == "TextEncodeAceStepAudio1.5"]
    ace10 = [nid for nid, c in classes.items() if c == "TextEncodeAceStepAudio"]
    return {
        "node_classes": sorted(set(classes.values())),
        "prompt_node": pos,
        "prompt_nodes": pos_nodes,
        "negative_node": neg,
        "negative_nodes": neg_nodes,
        "latent_nodes": [nid for nid, c in classes.items() if c in LATENTS],
        "output_nodes": outputs,
        "model_family": "ace_step_1.5" if ace15 else ("ace_step_1.0" if ace10 else ("stable_audio" if any(c == "ConditioningStableAudio" for c in classes.values()) else "unknown")),
        "supports": {"bpm": bool(ace15), "key": bool(ace15), "lyrics": bool(ace15 or ace10), "seed": True,
                     "negative_prompt": bool(neg_nodes)},
    }


def patch_workflow(wf: dict, req: GenerationRequest, prefix: str) -> tuple[dict, list[str]]:
    wf = copy.deepcopy(wf)
    info = describe_workflow(wf)
    if info["prompt_node"] is None:
        raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, "Could not find the prompt text node in the workflow",
                                 hint="The workflow needs a CLIPTextEncode or ACE-Step text encoder connected to the sampler.")
    if not info["output_nodes"]:
        raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, "Workflow has no SaveAudio/PreviewAudio node")
    p = req.params
    seed = req.seed if req.seed is not None else random.SystemRandom().randrange(2**48)
    patched: list[str] = []
    for nid in info["prompt_nodes"]:  # every sampler's prompt (base + refiner passes)
        node = wf[nid]
        field = TEXT_ENCODERS[node["class_type"]]
        node["inputs"][field] = req.prompt
        patched.append(f"{node['class_type']}.{field}")
    if p.get("negative_prompt") is not None:
        for nid in info["negative_nodes"]:
            wf[nid]["inputs"]["text"] = p["negative_prompt"]
            patched.append("negative CLIPTextEncode.text")
    for nid, n in wf.items():
        c, inp = n["class_type"], n.setdefault("inputs", {})
        if c in LATENTS:
            inp["seconds"] = float(req.seconds)
            if "batch_size" in inp:
                inp["batch_size"] = 1
            patched.append(f"{c}.seconds")
        elif c == "ConditioningStableAudio":
            inp["seconds_start"] = 0.0
            inp["seconds_total"] = float(req.seconds)
            patched.append("ConditioningStableAudio.seconds_total")
        elif c in SAMPLERS and SAMPLERS[c] in inp and not isinstance(inp[SAMPLERS[c]], list):
            inp[SAMPLERS[c]] = int(seed)
            patched.append(f"{c}.{SAMPLERS[c]}")
            if c in ("KSampler", "KSamplerAdvanced"):
                if p.get("steps"):
                    inp["steps"] = int(p["steps"])
                    patched.append(f"{c}.steps")
                if p.get("cfg") is not None:
                    inp["cfg"] = float(p["cfg"])
                    patched.append(f"{c}.cfg")
        elif c == "TextEncodeAceStepAudio1.5":
            inp["seed"] = int(seed) % (2**63)
            inp["duration"] = float(req.seconds)
            if p.get("bpm"):
                inp["bpm"] = int(round(float(p["bpm"])))
            if p.get("key"):
                inp["keyscale"] = p["key"]
            if p.get("beats_per_bar") and str(p["beats_per_bar"]) in ("2", "3", "4", "6"):
                inp["timesignature"] = str(p["beats_per_bar"])
            inp["lyrics"] = p.get("lyrics") or "[instrumental]"
            patched.append("TextEncodeAceStepAudio1.5.{seed,duration,bpm,keyscale,timesignature,lyrics}")
        elif c == "TextEncodeAceStepAudio":
            inp["lyrics"] = p.get("lyrics") or "[instrumental]"
            patched.append("TextEncodeAceStepAudio.lyrics")
        elif c in OUTPUTS:
            inp["filename_prefix"] = prefix
    return wf, patched


def ace_keyscale(key: str | None) -> str | None:
    """Normalize 'D minor' / 'Dm' / 'f# major' to ACE-Step's 'D minor' form."""
    if not key:
        return None
    k = key.strip()
    import re

    m = re.match(r"^([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major)?$", k)
    if not m:
        return None
    root = m.group(1).upper() + m.group(2)
    quality = "minor" if m.group(3) in ("m", "min", "minor") else "major"
    return f"{root} {quality}"


class _ComfySession(Session):
    def __init__(self, backend: "ComfyUIBackend"):
        self.b = backend

    def generate(self, request: GenerationRequest) -> Path:
        b = self.b
        wf_path = b.workflow_for(request.params.get("kind", "sfx"))
        wf = b.load_workflow(wf_path)
        prefix = f"hlga/{uuid.uuid4().hex}"
        if request.params.get("key"):
            request.params["key"] = ace_keyscale(request.params["key"]) or request.params["key"]
        patched_wf, _ = patch_workflow(wf, request, prefix)
        client_id = uuid.uuid4().hex
        resp = b.http("POST", "/prompt", {"prompt": patched_wf, "client_id": client_id})
        if resp.get("node_errors"):
            raise E.AudioPluginError(E.BACKEND_ERROR, "ComfyUI rejected the workflow", node_errors=resp["node_errors"])
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            raise E.AudioPluginError(E.BACKEND_ERROR, f"ComfyUI did not queue the prompt: {resp}")
        deadline = time.monotonic() + b.timeout
        entry = None
        while time.monotonic() < deadline:
            hist = b.http("GET", f"/history/{urllib.parse.quote(prompt_id)}")
            entry = hist.get(prompt_id) if isinstance(hist, dict) else None
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                    detail = msgs[0][1].get("exception_message", "") if msgs else ""
                    raise E.AudioPluginError(E.BACKEND_ERROR, f"ComfyUI execution failed: {detail}".strip())
                if status.get("completed", True) and entry.get("outputs"):
                    break
            b.sleep(b.poll_interval)
        else:
            try:
                b.http("POST", "/interrupt", {})
                b.http("POST", "/queue", {"delete": [prompt_id]})
            except E.AudioPluginError:
                pass
            raise E.AudioPluginError(E.GENERATION_TIMEOUT, f"ComfyUI did not finish within {b.timeout:.0f}s (interrupted)")
        files = []
        for out in (entry or {}).get("outputs", {}).values():
            for item in out.get("audio", []) or []:
                files.append(item)
        if not files:
            raise E.AudioPluginError(E.INVALID_OUTPUT, "ComfyUI finished without an audio output")
        item = files[0]
        q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "output")})
        data = b.http("GET", f"/view?{q}", raw=True)
        from ..codecs_io import decode_any

        with tempfile.TemporaryDirectory(prefix=".hlga-comfy-", dir=request.output_path.parent) as tmp:
            src = Path(tmp) / Path(item["filename"]).name
            src.write_bytes(data)
            wav_bytes = decode_any(src, b.ffmpeg)
        with open(request.output_path, "xb") as fh:
            fh.write(wav_bytes)
        return request.output_path


class ComfyUIBackend(AudioBackend):
    name = "comfyui"
    is_ai_model = True

    def __init__(self, *, url: str = "http://127.0.0.1:8188", workflow_sfx: str = "", workflow_music: str = "",
                 allow_remote: bool = False, timeout: float = 600, ffmpeg: str | None = None,
                 opener: Callable[..., Any] | None = None, poll_interval: float = 1.0):
        self.url = (url or "http://127.0.0.1:8188").rstrip("/")
        self.workflow_sfx = workflow_sfx.strip()
        self.workflow_music = workflow_music.strip()
        self.allow_remote = allow_remote
        self.timeout = float(timeout)
        self.ffmpeg = ffmpeg
        self._open = opener or urllib.request.urlopen
        self.poll_interval = poll_interval
        self.sleep = time.sleep
        self.supports = {"sfx": bool(self.workflow_sfx or self.workflow_music), "music": bool(self.workflow_music or self.workflow_sfx),
                         "ambience": True, "bpm": False, "key": False, "seed": True, "lyrics": False,
                         "negative_prompt": False, "max_seconds": 1000}
        try:
            for path in {self.workflow_music, self.workflow_sfx} - {""}:
                d = describe_workflow(self.load_workflow(path))
                for k, v in d["supports"].items():
                    self.supports[k] = self.supports.get(k) or v
        except E.AudioPluginError:
            pass

    def supports_for(self, kind: str) -> dict[str, Any]:
        out = dict(self.supports)
        try:
            d = describe_workflow(self.load_workflow(self.workflow_for(kind)))
            out.update(d["supports"])
            out["model_family"] = d["model_family"]
        except E.AudioPluginError:
            pass
        return out

    @property
    def configured(self) -> bool:
        return bool(self.workflow_sfx or self.workflow_music)

    def workflow_for(self, kind: str) -> str:
        if kind in ("music", "stinger", "ambience"):
            path = self.workflow_music or self.workflow_sfx
        else:
            path = self.workflow_sfx or self.workflow_music
        if not path:
            raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, "No ComfyUI workflow configured",
                                     hint="Export an audio workflow with Workflow > Export (API) and set comfyui_workflow_music / comfyui_workflow_sfx.")
        return path

    @staticmethod
    def load_workflow(path: str) -> dict:
        p = Path(path).expanduser()
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, f"ComfyUI workflow file not found: {p}") from None
        except (OSError, ValueError) as exc:
            raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, f"Cannot read workflow {p.name}: {exc}") from None
        if isinstance(data, dict) and "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        describe_workflow(data)
        return data

    def _check_host(self) -> None:
        host = urllib.parse.urlparse(self.url).hostname or ""
        if host not in LOOPBACK and not self.allow_remote:
            raise E.AudioPluginError(E.PATH_REJECTED, f"ComfyUI URL {self.url} is not on this computer",
                                     hint="Use http://127.0.0.1:8188, or enable comfyui_allow_remote if you trust that host.")

    def http(self, method: str, path: str, body: Any = None, *, raw: bool = False) -> Any:
        self._check_host()
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with self._open(req, timeout=60) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1500]
            try:
                parsed = json.loads(detail)
            except ValueError:
                parsed = {"raw": detail}
            err = parsed.get("error", {})
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise E.AudioPluginError(E.BACKEND_ERROR, f"ComfyUI {path.split('?')[0]} returned {exc.code}: {msg or detail[:200]}",
                                     node_errors=parsed.get("node_errors")) from None
        except (urllib.error.URLError, OSError) as exc:
            raise E.AudioPluginError(E.BACKEND_NOT_READY, f"ComfyUI is not reachable at {self.url}: {exc}",
                                     hint="Start ComfyUI (default port 8188) and try again.") from None
        if raw:
            return payload
        return json.loads(payload.decode("utf-8")) if payload else {}

    def status(self, *, deep: bool = False) -> BackendStatus:
        st = BackendStatus(self.name, configured=self.configured, ready=False, is_ai_model=True)
        st.details["url"] = self.url
        workflows = {}
        for label, path in (("sfx", self.workflow_sfx), ("music", self.workflow_music)):
            if not path:
                continue
            try:
                workflows[label] = {"path": path, **describe_workflow(self.load_workflow(path))}
            except E.AudioPluginError as exc:
                workflows[label] = {"path": path, "error": exc.message}
                st.problems.append(f"{label} workflow: {exc.message}")
        st.details["workflows"] = workflows
        st.details["supports"] = self.supports
        if not self.configured:
            st.problems.append("No ComfyUI workflow configured")
            st.setup_steps = [
                "Start ComfyUI and build/open a working audio workflow (Stable Audio Open or ACE-Step 1.5 template).",
                "Run it once in ComfyUI to confirm it works, then use Workflow > Export (API).",
                "hermes config set plugins.entries.hermes-local-game-audio.settings.comfyui_workflow_music <path-to-json>",
                "Optionally set comfyui_workflow_sfx to a Stable Audio Open workflow for sound effects.",
            ]
            return st
        if not deep:
            st.problems.append("ComfyUI server not contacted; run audio_status with deep_check=true")
            return st
        try:
            stats = self.http("GET", "/system_stats")
            st.details["system"] = {"comfyui_version": stats.get("system", {}).get("comfyui_version"),
                                    "devices": [{"name": d.get("name"), "type": d.get("type"), "vram_total": d.get("vram_total")} for d in stats.get("devices", [])]}
            missing = []
            for wf in workflows.values():
                for cls in wf.get("node_classes", []):
                    info = self.http("GET", f"/object_info/{urllib.parse.quote(cls)}")
                    if not info:
                        missing.append(cls)
            if missing:
                st.problems.append(f"ComfyUI is missing node types: {sorted(set(missing))}")
            else:
                st.ready = not any("error" in w for w in workflows.values())
        except E.AudioPluginError as exc:
            st.problems.append(exc.message)
        return st

    @contextmanager
    def session(self) -> Iterator[Session]:
        self._check_host()
        if not self.configured:
            raise E.AudioPluginError(E.BACKEND_NOT_CONFIGURED, "No ComfyUI workflow configured",
                                     hint="Set comfyui_workflow_music and/or comfyui_workflow_sfx (Export (API) JSON).")
        yield _ComfySession(self)
