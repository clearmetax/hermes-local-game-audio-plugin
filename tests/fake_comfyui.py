"""In-process fake of the ComfyUI HTTP API used by the comfyui backend tests.

Implements /prompt, /history/<id>, /view, /system_stats, /object_info/<class>,
/interrupt and /queue. "Executing" a prompt renders a tone of the requested
length (EmptyLatentAudio / ACE-Step seconds) and stores it as a real FLAC
(verbatim subframes, valid CRCs) the way SaveAudio does.
"""

from __future__ import annotations

import json
import math
import struct
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

KNOWN = {"CheckpointLoaderSimple", "CLIPLoader", "CLIPTextEncode", "ConditioningStableAudio", "EmptyLatentAudio", "KSampler",
         "VAEDecodeAudio", "SaveAudio", "TextEncodeAceStepAudio1.5", "EmptyAceStep1.5LatentAudio", "ConditioningZeroOut",
         "ModelSamplingAuraFlow"}


def _crc8(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def _crc16(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x8005) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


class _BW:
    def __init__(self):
        self.bits: list[str] = []

    def put(self, v: int, n: int) -> None:
        self.bits.append(format(v & ((1 << n) - 1), f"0{n}b"))

    def bytes(self) -> bytes:
        s = "".join(self.bits)
        s += "0" * (-len(s) % 8)
        return int(s, 2).to_bytes(len(s) // 8, "big") if s else b""


def flac_encode(chans: list[list[int]], rate: int, bps: int = 16, block: int = 4096) -> bytes:
    n = len(chans[0])
    nch = len(chans)
    si = struct.pack(">HH", block, block) + (0).to_bytes(3, "big") + (0).to_bytes(3, "big")
    packed = (rate << 44) | ((nch - 1) << 41) | ((bps - 1) << 36) | n
    si += packed.to_bytes(8, "big") + b"\0" * 16
    out = b"fLaC" + bytes([0x80]) + len(si).to_bytes(3, "big") + si
    for fi, start in enumerate(range(0, n, block)):
        bs = min(block, n - start)
        w = _BW()
        w.put(0x3FFE, 14)
        w.put(0, 1)
        w.put(0, 1)
        w.put(7, 4)  # block size: 16-bit value follows
        w.put(0, 4)  # sample rate from STREAMINFO
        w.put(nch - 1, 4)
        w.put(4 if bps == 16 else 0, 3)
        w.put(0, 1)
        assert fi < 128
        w.put(fi, 8)  # UTF-8 coded frame number (< 128 -> one byte)
        w.put(bs - 1, 16)
        head = w.bytes()
        frame = bytearray(head + bytes([_crc8(head)]))
        w2 = _BW()
        for c in chans:
            w2.put(0, 1)  # padding
            w2.put(1, 6)  # VERBATIM
            w2.put(0, 1)  # no wasted bits
            for v in c[start:start + bs]:
                w2.put(v, bps)
        frame += w2.bytes()
        frame += _crc16(bytes(frame)).to_bytes(2, "big")
        out += bytes(frame)
    return out


class FakeComfy:
    def __init__(self, *, fail: str | None = None, slow: bool = False):
        self.fail = fail
        self.slow = slow
        self.prompts: list[dict] = []
        self.history: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.interrupted = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, code: int, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/system_stats":
                    return self._send(200, {"system": {"comfyui_version": "fake"}, "devices": [{"name": "cpu", "type": "cpu", "vram_total": 0}]})
                if u.path.startswith("/object_info/"):
                    cls = u.path.split("/object_info/", 1)[1]
                    from urllib.parse import unquote
                    cls = unquote(cls)
                    return self._send(200, {cls: {}} if cls in KNOWN else {})
                if u.path.startswith("/history/"):
                    pid = u.path.rsplit("/", 1)[1]
                    return self._send(200, {pid: fake.history[pid]} if pid in fake.history else {})
                if u.path == "/view":
                    q = parse_qs(u.query)
                    key = q["subfolder"][0] + "/" + q["filename"][0]
                    return self._send(200, fake.files[key], "audio/flac") if key in fake.files else self._send(404, {})
                return self._send(404, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/prompt":
                    return self._send(*fake.queue(body))
                if self.path == "/interrupt":
                    fake.interrupted += 1
                    return self._send(200, {})
                if self.path == "/queue":
                    return self._send(200, {})
                return self._send(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()

    def queue(self, body: dict):
        wf = body["prompt"]
        self.prompts.append(wf)
        if self.fail == "reject":
            return 400, {"error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
                         "node_errors": {"3": {"errors": [{"message": "Value not in list: ckpt_name"}]}}}
        pid = uuid.uuid4().hex
        if self.slow:
            self.history[pid] = {"status": {"status_str": "running", "completed": False}, "outputs": {}}
            return 200, {"prompt_id": pid, "number": 1, "node_errors": {}}
        if self.fail == "exec":
            self.history[pid] = {"status": {"status_str": "error", "completed": False,
                                            "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]}, "outputs": {}}
            return 200, {"prompt_id": pid, "number": 1, "node_errors": {}}
        seconds = 1.0
        prefix = "ComfyUI"
        out_node = None
        for nid, n in wf.items():
            if n["class_type"] in ("EmptyLatentAudio", "EmptyAceStep1.5LatentAudio"):
                seconds = float(n["inputs"]["seconds"])
            if n["class_type"] == "SaveAudio":
                prefix = n["inputs"]["filename_prefix"]
                out_node = nid
        rate = 44100
        frames = int(seconds * rate)
        left = [int(12000 * math.sin(2 * math.pi * 220 * i / rate) * (0.6 + 0.4 * math.sin(2 * math.pi * 2 * i / rate))) for i in range(frames)]
        right = [int(v * 0.9) for v in left]
        sub, name = (prefix.rsplit("/", 1) + [""])[:2] if "/" in prefix else ("", prefix)
        fname = f"{name}_00001_.flac"
        self.files[f"{sub}/{fname}"] = flac_encode([left, right], rate)
        self.history[pid] = {"status": {"status_str": "success", "completed": True, "messages": []},
                             "outputs": {out_node: {"audio": [{"filename": fname, "subfolder": sub, "type": "output"}]}}}
        return 200, {"prompt_id": pid, "number": 1, "node_errors": {}}
