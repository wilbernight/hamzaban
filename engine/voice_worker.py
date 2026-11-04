"""Aava voice worker: natural Persian speech (Orpheus-3B, ~2,500 h of Persian).

Runs in its own environment (venv-aava: MLX + SNAC), listens on 127.0.0.1,
is started by the engine the first time an Aava voice is used, and exits
after IDLE_EXIT seconds without work so its ~2.5 GB go back to the system.

    POST /synth  {"texts": ["...", ...], "voice": "male" | "female" | null}
         -> {"wavs": [base64 wav, ...]}   (24 kHz mono, one per text)
    GET  /health -> {"ok": true, "loaded": bool, "voices": [...]}

All sentences of a request are generated together in one batch.
"""
import base64
import io
import json
import os
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("HAMZABAN_VOICE_PORT", "17861"))
MODEL_DIR = os.environ.get("HAMZABAN_AAVA_DIR", os.path.join(HERE, "..", "aava-mlx-4"))
IDLE_EXIT = float(os.environ.get("HAMZABAN_VOICE_IDLE", "600"))
MAX_BATCH = 6

# MLX streams belong to the thread that created them: load and run on one thread
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aava")
_aava = None
_last_used = time.time()
_busy = threading.Event()


def _model():
    global _aava
    if _aava is None:
        sys.path.insert(0, HERE)
        from aava_mlx import Aava

        _aava = Aava(MODEL_DIR, os.path.join(MODEL_DIR, "voices.json"))
    return _aava


def _wav_bytes(wav) -> bytes:
    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes((np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _synth(texts, voice):
    a = _model()
    voice = voice if voice in a.voices else None
    out = []
    for i in range(0, len(texts), MAX_BATCH):
        chunk = texts[i:i + MAX_BATCH]
        try:
            out += a.say_many(chunk, voice)
        except RuntimeError as e:  # GPU memory is shared with other apps: retry in halves
            if "Memory" not in str(e) or len(chunk) == 1:
                raise
            half = len(chunk) // 2
            out += a.say_many(chunk[:half], voice) + a.say_many(chunk[half:], voice)
    return [_wav_bytes(w) for w in out]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            voices = list(_aava.voices) if _aava else []
            self._send(200, json.dumps({"ok": True, "loaded": _aava is not None, "voices": voices}).encode())
        else:
            self._send(404, b"{}")

    def do_POST(self):
        global _last_used
        if self.path != "/synth" or self.headers.get("Origin"):
            return self._send(403, b"{}")  # only the local engine talks to this worker
        _busy.set()
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            texts = [str(t)[:600] for t in body.get("texts") or [body.get("text", "")]][:24]
            wavs = _pool.submit(_synth, texts, body.get("voice")).result()
            self._send(200, json.dumps({"wavs": [base64.b64encode(w).decode() for w in wavs]}).encode())
        except Exception as e:  # report instead of dying
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())
        finally:
            _last_used = time.time()
            _busy.clear()


def idle_watch(server):
    while True:
        time.sleep(30)
        if time.time() - _last_used > IDLE_EXIT and not _busy.is_set():
            server.shutdown()
            return


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=idle_watch, args=(srv,), daemon=True).start()
    print(f"aava voice worker on {PORT}", flush=True)
    srv.serve_forever()
