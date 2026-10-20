"""Stand-in for agent_audio.mcp_server speaking real MCP stdio JSON-RPC.

Behaviour is selected with FAKE_MCP_MODE: ok | error | slow | crash | badwav | notready | wrongpath.
Writes a short sine WAV to the requested output_path (like the real server).
"""

import json
import math
import os
import struct
import sys
import time

MODE = os.environ.get("FAKE_MCP_MODE", "ok")


def wav_bytes(seconds, rate=44100):
    n = int(seconds * rate)
    frames = b"".join(struct.pack("<ff", 0.5 * math.sin(i * 0.05) * (1 - i / n), 0.5 * math.sin(i * 0.05) * (1 - i / n)) for i in range(n))
    fmt = struct.pack("<IHHIIHH", 16, 3, 2, rate, rate * 8, 8, 32)
    return b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(frames)) + frames


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def result(i, text, structured=None, error=False):
    r = {"content": [{"type": "text", "text": text}], "isError": error}
    if structured is not None:
        r["structuredContent"] = structured
    send({"jsonrpc": "2.0", "id": i, "result": r})


calls = 0
for line in sys.stdin:
    msg = json.loads(line)
    method, i = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": i, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "agent-audio", "version": "fake"}}})
    elif method == "notifications/initialized":
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "hello"}})
    elif method == "tools/list":
        tools = [] if MODE == "notools" else [{"name": "audio_status", "inputSchema": {}}, {"name": "generate_audio", "inputSchema": {}}]
        send({"jsonrpc": "2.0", "id": i, "result": {"tools": tools}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name == "audio_status":
            ready = MODE != "notready"
            result(i, json.dumps({"runtime_ready": ready}), {"runtime_ready": ready, "selected_backend": "fake"})
            continue
        calls += 1
        if MODE == "crash" or (MODE == "crash2" and calls == 2):
            sys.exit(3)
        if MODE == "slow":
            time.sleep(30)
        if MODE == "error":
            result(i, "Error executing tool generate_audio: boom", error=True)
            continue
        if MODE == "notready":
            result(i, "Error executing tool generate_audio: Stable Audio runtime is not ready for 'tflite'. Run the bootstrap installer first.", error=True)
            continue
        out = args["output_path"]
        if MODE == "wrongpath":
            out = out + ".other.wav"
        with open(out, "xb") as fh:
            fh.write(b"RIFFjunk" if MODE == "badwav" else wav_bytes(float(args["seconds"])))
        result(i, out, {"result": out})
    elif i is not None:
        send({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "nope"}})
