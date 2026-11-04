"""OmniVoice voice worker: fast, clear Persian speech with a steady voice.

OmniVoice (k2-fsa, the engine behind VoiceStudio) run through mlx-audio on the
Apple GPU. It is not autoregressive: every sentence is generated at a length
chosen up front, so a dubbing slot is hit exactly instead of time-stretching
afterwards. Measured on an M5 (16 steps): about 6x faster than real time,
Whisper character error 0.00-0.07 on Persian when sentences are generated one
at a time. In a batch, short sentences next to long ones come out garbled or
cut off (character error up to 1.0), and batching was not even faster, so
every sentence gets its own generate call.

Runs in its own environment (venv-omni), listens on 127.0.0.1, is started
by the engine on first use and exits after IDLE_EXIT seconds without work.

    POST /synth {"texts": [...], "voice": "male" | "female" | null,
                 "ref_wav": path?, "ref_text": str?,     (clone any voice)
                 "slots": [seconds | null, ...]?}         (time available per text)
         -> {"wavs": [base64 wav, ...]}  (24 kHz mono, one per text)
    GET  /health -> {"ok": true, "loaded": bool, "voices": [...]}
"""
import base64
import io
import json
import os
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
PORT = int(os.environ.get("HAMZABAN_OMNI_PORT", "17862"))
MODEL_DIR = os.environ.get("HAMZABAN_OMNI_DIR", os.path.join(APP, "omnivoice-bf16"))
VOICES_DIR = os.environ.get("HAMZABAN_OMNI_VOICES", os.path.join(APP, "omnivoice-voices"))
IDLE_EXIT = float(os.environ.get("HAMZABAN_VOICE_IDLE", "600"))
STEPS = int(os.environ.get("HAMZABAN_OMNI_STEPS", "16"))  # 32 = a little cleaner, half the speed
# Memory: the 0.6B backbone quantized at load (8 bits: 1.2 -> 0.6 GB), the audio codec in
# bfloat16 (0.73 -> 0.37 GB), and MLX told to give freed buffers back instead of keeping
# a cache as large as the biggest sentence ever generated (~2 GB).
QBITS = int(os.environ.get("HAMZABAN_OMNI_QBITS", "0"))
CODEC_DTYPE = os.environ.get("HAMZABAN_OMNI_CODEC_DTYPE", "")
CACHE_MB = int(os.environ.get("HAMZABAN_OMNI_CACHE_MB", "256"))
TOKENS_PER_SECOND = 25.0   # HiggsAudio codec frame rate (24000 / 960)
# Asked for a shorter length than natural, OmniVoice drops words instead of speaking
# faster (Whisper character error 0.02 at 1.1x, up to 0.38 at 1.2x and 0.75 at 1.3x).
# The engine speeds the rest up afterwards with a pitch-keeping time stretch, which
# loses nothing (0.02 even at 1.4x).
MAX_SQUEEZE = 1.1

# MLX streams belong to the thread that created them: load and run on one thread
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="omni")
_model = None
_voices = {}   # "male"/"female" -> {"wav": path, "text": transcript}
_refs = {}     # (path, mtime) -> (tokens, text)
_last_used = time.time()
_busy = threading.Event()


def plan_durations(natural, slots, max_squeeze=MAX_SQUEEZE):
    """Seconds to generate per sentence: the natural length, shortened to fit
    its slot when needed, but never squeezed beyond max_squeeze."""
    out = []
    for i, est in enumerate(natural):
        slot = slots[i] if slots and i < len(slots) else None
        if not est or est <= 0:
            out.append(None)            # let the model decide
        elif slot and slot < est:
            out.append(max(slot, est / max_squeeze))
        else:
            out.append(est)
    return out


def _load():
    global _model, _voices
    if _model is None:
        import mlx.core as mx
        import mlx.nn as nn
        from mlx_audio.tts.utils import load_model

        if CACHE_MB >= 0:
            mx.set_cache_limit(CACHE_MB * 1024 * 1024)
        model = load_model(MODEL_DIR)
        if QBITS:
            nn.quantize(model.backbone, group_size=64, bits=QBITS)
        if CODEC_DTYPE:
            model.audio_tokenizer.set_dtype(getattr(mx, CODEC_DTYPE))
        mx.eval(model.parameters())
        mx.clear_cache()
        _model = model
        try:
            with open(os.path.join(VOICES_DIR, "voices.json"), encoding="utf-8") as f:
                raw = json.load(f)
            _voices = {k: {"wav": os.path.join(VOICES_DIR, os.path.basename(v["wav"])), "text": v["text"]}
                       for k, v in raw.items()}
        except OSError:
            _voices = {}
    return _model


def _reference(path, text):
    """Encode a reference clip once and keep it (it is reused for every sentence)."""
    from mlx_audio.tts.models.omnivoice.utils import create_voice_clone_prompt

    key = (path, os.path.getmtime(path))
    if key not in _refs:
        _refs[key] = (create_voice_clone_prompt(path, tokenizer=_model.audio_tokenizer, ref_text=text), text)
    return _refs[key]


def _natural_seconds(texts, ref_tokens, ref_text):
    from mlx_audio.tts.models.omnivoice.duration import RuleDurationEstimator

    est = RuleDurationEstimator()
    return [est.estimate_duration(t, ref_text, ref_tokens.shape[0]) / TOKENS_PER_SECOND for t in texts]


def _wav_bytes(wav) -> bytes:
    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes((np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def _generate(text, tokens, ref_text, duration):
    import numpy as np

    res = _model.generate_batch([text], language="fa", ref_text=[ref_text], ref_tokens=[tokens],
                                duration_s=[duration], num_steps=STEPS, max_batch_size=1)
    return np.array(res[0].audio, dtype=np.float32).reshape(-1)


def _synth(texts, voice, ref_wav, ref_text, slots):
    _load()
    if ref_wav and ref_text:
        tokens, text = _reference(ref_wav, ref_text)
    else:
        v = _voices.get(voice) or next(iter(_voices.values()), None)
        if v is None:
            raise RuntimeError("no OmniVoice reference voices (voices.json) found")
        tokens, text = _reference(v["wav"], v["text"])
    durations = plan_durations(_natural_seconds(texts, tokens, text), slots)
    return [_wav_bytes(_generate(t, tokens, text, d)) for t, d in zip(texts, durations)]


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
            self._send(200, json.dumps({"ok": True, "loaded": _model is not None,
                                        "voices": list(_voices)}).encode())
        else:
            self._send(404, b"{}")

    def do_POST(self):
        global _last_used
        if self.path != "/synth" or self.headers.get("Origin"):
            return self._send(403, b"{}")  # only the local engine talks to this worker
        _busy.set()
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            texts = [str(t)[:800] for t in body.get("texts") or []][:32]
            slots = body.get("slots")
            wavs = _pool.submit(_synth, texts, body.get("voice"), body.get("ref_wav"),
                                body.get("ref_text"), slots).result()
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
    print(f"omnivoice worker on {PORT}", flush=True)
    srv.serve_forever()
