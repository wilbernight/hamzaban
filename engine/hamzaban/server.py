"""Local HTTP API used by the Thunderbird add-on and the browser extension.

Listens on 127.0.0.1 only, and answers only browser-extension origins, so a
web page cannot use it (also rejects foreign Host headers against DNS
rebinding).
"""
import json
import logging
import re
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import config, langs, persian
from .asr import Transcriber
from .dubbing import IDLE_STOP, DubJob
from .dubcache import AsrCache, TtsCache
from .translator import TranslationError, Translator
from .tts import PersianTTS, release_other_voices, to_wav_bytes

log = logging.getLogger("hamzaban")

MAX_TEXT = 30000
EXTENSION_ORIGINS = ("moz-extension://", "chrome-extension://", "safari-web-extension://")
_MODEL_NAME = re.compile(r"^[\w.:/-]{1,80}$")


class Engine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.cache_dir = config.CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.translator = Translator(cfg, self.cache_dir / "translations.sqlite")
        # what earlier dubs heard and spoke: never transcribe or speak the same thing twice
        self.asr_cache = AsrCache(self.cache_dir / "asr", cfg["whisper_model"])
        self.tts_cache = TtsCache(self.cache_dir / "tts")
        self._asr = None
        self._tts = None
        self._lazy = threading.Lock()
        self.jobs: dict[str, DubJob] = {}
        self.jobs_lock = threading.Lock()

    @property
    def asr(self) -> Transcriber:
        with self._lazy:
            if self._asr is None:
                self._asr = Transcriber(self.cfg["whisper_model"], int(self.cfg["mlx_cache_mb"]))
            return self._asr

    @property
    def tts(self) -> PersianTTS:
        with self._lazy:
            if self._tts is None:
                self._tts = PersianTTS(config.PARSIGO_DIR, config.USER_VOICES_DIR)
            return self._tts

    def warm_voice(self, voice):
        """Start the voice (and load its model) while the video is being resolved.
        ParSiGo is loaded only for its own voices: a dub with OmniVoice never needs it."""
        from . import aava, omni

        if (omni.is_omni(voice) or voice == "clone") and omni.available():
            fn = lambda: omni.synth_many(["سلام."], "omni_female")
        elif aava.is_aava(voice) and aava.available():
            return
        else:  # the first ParSiGo sentence pays ~15 s of one-off setup
            fn = lambda: self.tts.synth("سلام.", voice)
        threading.Thread(target=lambda: _quietly(fn), daemon=True).start()

    def warm_for_dub(self, model):
        """Load Whisper and the dubbing model while yt-dlp finds the audio."""
        import numpy as np

        def asr():
            self.asr.transcribe(np.zeros(16000, dtype=np.float32))

        def mt():
            # the e-mail model (8 GB) next to Whisper, the dub model and a voice makes
            # a 16 GB Mac swap: let it go now, it loads again for the next e-mail
            if self.cfg["email_model"] != model:
                self.translator.unload(self.cfg["email_model"])
                if self.cfg.get("email_model_en"):
                    self.translator.unload(self.cfg["email_model_en"])
            self.translator.translate("Hello.", model, "en")

        for fn in (asr, mt):
            threading.Thread(target=lambda f=fn: _quietly(f), daemon=True).start()

    def model_for(self, body: dict) -> str:
        model = body.get("model") or ""
        if model and _MODEL_NAME.match(model):
            return model
        return self.cfg["dub_model" if body.get("purpose") == "dub" else "email_model"]

    def reap(self):
        """Stop jobs nobody watches any more, delete old dub audio, unload an idle
        ParSiGo and keep the caches bounded."""
        last_prune = 0.0
        while True:
            time.sleep(60)
            now = time.time()
            with self.jobs_lock:
                items = list(self.jobs.items())
            for job_id, job in items:
                if now - job.last_poll > IDLE_STOP:
                    job.stop()
                if now - job.last_poll > 6 * 3600:
                    self.jobs.pop(job_id, None)
                    shutil.rmtree(job.dir, ignore_errors=True)
            if self._tts is not None and self._tts.release_if_idle():
                log.info("parsigo unloaded (idle)")
            if now - last_prune > 3600:
                last_prune = now
                _quietly(self.tts_cache.prune)
                _quietly(self.asr_cache.prune)


def _quietly(fn):
    try:
        fn()
    except Exception:
        log.exception("warm-up failed")


def clean_old_dub_dirs(cache_dir):
    root = cache_dir / "dub"
    if not root.exists():
        return
    cutoff = time.time() - 24 * 3600
    for d in root.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


class LocalExtensionsOnly:
    """Plain ASGI middleware (streams and client disconnects pass straight
    through): loopback Host only, browser-extension Origins only, plus CORS."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "").rsplit(":", 1)[0]
        origin = headers.get("origin")
        if host not in ("127.0.0.1", "localhost") or (origin and not origin.startswith(EXTENSION_ORIGINS)):
            return await JSONResponse({"error": "forbidden"}, status_code=403)(scope, receive, send)
        cors = [] if not origin else [
            (b"access-control-allow-origin", origin.encode("latin-1")),
            (b"access-control-allow-methods", b"GET, POST, OPTIONS"),
            (b"access-control-allow-headers", b"Content-Type"),
            (b"access-control-allow-private-network", b"true"),
            (b"vary", b"Origin"),
        ]
        if scope["method"] == "OPTIONS":
            resp = Response(status_code=204)
            resp.raw_headers.extend(cors)
            return await resp(scope, receive, send)

        async def send_with_cors(message):
            if cors and message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + cors
            await send(message)

        await self.app(scope, receive, send_with_cors)


cfg = config.load()
engine = Engine(cfg)
app = FastAPI(title="Hamzaban", version=config.VERSION, docs_url=None, redoc_url=None)
app.add_middleware(LocalExtensionsOnly)


# ------------------------------------------------------------------ status
@app.get("/api/health")
def health():
    tr = engine.translator
    return {
        "ok": True, "name": "Hamzaban", "version": config.VERSION,
        "email_model": cfg["email_model"], "dub_model": cfg["dub_model"],
        "email_model_ready": tr.model_available(cfg["email_model"]),
        "dub_model_ready": tr.model_available(cfg["dub_model"]),
        "tts_ready": (config.PARSIGO_DIR / "model" / "onnx" / "manifest.json").exists(),
        "whisper_model": cfg["whisper_model"],
    }


@app.get("/api/voices")
def voices():
    return {"voices": engine.tts.voices() + [{"id": "clone", "name": "شبیه گوینده‌ی اصلی (آزمایشی)"}],
            "default": cfg["default_voice"]}


# ------------------------------------------------------------------ translation
@app.post("/api/translate")
def translate(body: dict):
    text = (body.get("text") or "")[:MAX_TEXT]
    try:
        out = engine.translator.translate(text, engine.model_for(body), body.get("source"))
    except TranslationError as e:
        raise HTTPException(503, str(e))
    return {"text": out}


@app.post("/api/translate/stream")
async def translate_stream(body: dict):
    text = (body.get("text") or "")[:MAX_TEXT]
    model = engine.model_for(body)

    async def events():
        try:
            async for ev in engine.translator.stream(text, model, body.get("source")):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except TranslationError as e:
            yield json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False) + "\n"
        except Exception as e:
            log.exception("stream failed")
            yield json.dumps({"type": "error", "message": f"خطای داخلی: {e}"}, ensure_ascii=False) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/api/detect")
def detect(text: str):
    code = langs.detect(text)
    return {"code": code, "name_fa": langs.name_fa(code)}


# ------------------------------------------------------------------ speech
@app.post("/api/tts")
def tts(body: dict):
    text = (body.get("text") or "").strip()[:2000]
    if not text and not body.get("phonemes"):
        raise HTTPException(400, "متن خالی است")
    t = engine.tts
    phonemes = str(body.get("phonemes") or "").strip()[:500]
    voice = body.get("voice") or cfg["default_voice"]
    if phonemes and _parsigo(voice):  # hear a phoneme fix exactly as it will be said
        with t.lock:
            audio = t.eng.synthesize_text(phonemes, str(t.voice_path(voice)))
        return Response(to_wav_bytes(audio, t.sample_rate), media_type="audio/wav")
    audio = t.synth(text, voice)
    return Response(to_wav_bytes(audio, t.sample_rate), media_type="audio/wav")


# ------------------------------------------------------------------ dubbing
@app.post("/api/dub/start")
def dub_start(body: dict):
    url = (body.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(400, "لینک ویدیو معتبر نیست")
    voice = body.get("voice") or cfg["default_voice"]
    # "fast" = the small dubbing model, "best" = the e-mail model (slower start, better Persian)
    model = cfg["email_model"] if body.get("quality") == "best" else cfg["dub_model"]
    t = float(body.get("t") or 0)
    speak = body.get("mode") != "subs"
    style = "written" if body.get("style") == "written" else cfg.get("dub_style", "spoken")
    captions = body.get("captions")
    if captions:
        cues = captions.get("cues") or []
        if not isinstance(cues, list) or len(cues) > 20000:
            raise HTTPException(400, "زیرنویس نامعتبر است")
        captions = {"cues": [{"start": c["start"], "end": c["end"], "text": str(c["text"])[:600]} for c in cues],
                    "duration": captions.get("duration"), "lang": captions.get("lang"),
                    "title": str(captions.get("title") or "")[:200]}
    with engine.jobs_lock:
        for job in list(engine.jobs.values()):  # same video again (reload, new tab): reuse
            if (job.page_url == url and job.voice == voice and job.model == model and job.speak == speak
                    and job.style == style and not job.stopped):
                return job.poll(t)
        # one dub at a time: an earlier job (other voice, other video) would compete for the GPU
        for other in engine.jobs.values():
            if not other.stopped:
                other.stop()
        job = DubJob(engine, url, body.get("media"), voice, t, model, captions, speak, body.get("cookies"),
                     can_live=bool(body.get("can_live")), style=style)
        engine.jobs[job.id] = job
    engine.warm_for_dub(model)
    if speak:
        engine.warm_voice(voice)
    threading.Thread(target=_quietly, args=(lambda: release_other_voices(voice if speak else ""),),
                     daemon=True).start()
    job.start()
    log.info("dub job %s for %s (voice %s, t=%.1f, %d site cookies%s)", job.id, url, voice, t, len(job.source.cookies),
             f"; extension: {str(body['cookie_note'])[:120]}" if body.get("cookie_note") else "")
    return job.poll(t)


def _job(job_id: str) -> DubJob:
    job = engine.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "این دوبله دیگر فعال نیست")
    return job


@app.get("/api/dub/{job_id}")
def dub_poll(job_id: str, t: float | None = None, since: int = 0, tsince: int | None = None):
    return _job(job_id).poll(t, since, tsince)


@app.get("/api/dub/{job_id}/audio/{sid}")
def dub_audio(job_id: str, sid: int):
    path = _job(job_id).audio_path(sid)
    if not path:
        raise HTTPException(404, "not ready")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})  # fixes rewrite it


@app.post("/api/dub/{job_id}/stop")
def dub_stop(job_id: str):
    job = _job(job_id)
    job.stop()
    return {"ok": True}


@app.post("/api/dub/{job_id}/live")
def dub_live(job_id: str, body: dict):
    """A chunk of the video's own sound from the extension (live mode):
    {t: video time it starts at, rate: playback speed, pcm: base64 int16 mono 16 kHz}."""
    import base64

    import numpy as np

    job = _job(job_id)
    if not job.live:
        return {"ok": False}
    pcm = str(body.get("pcm") or "")
    if len(pcm) > 400_000:  # ~6 s per chunk at most
        raise HTTPException(413, "تکه‌ی صدا بیش از حد بزرگ است")
    if body.get("note"):  # what the extension does in live mode (time shift on/off and why)
        log.info("job %s live: %s", job_id, str(body["note"])[:160])
    samples = np.frombuffer(base64.b64decode(pcm), dtype="<i2").astype(np.float32) / 32768
    job.source.feed(float(body.get("t") or 0), samples, float(body.get("rate") or 1))
    return {"ok": True}


# ------------------------------------------------------------ the user's words
# pronounce.txt (word = how to say it) and idioms.txt (english = plain meaning =
# Persian), edited from the extension; both are re-read on every change.
LEXICONS = {"pronounce": (config.APP_HOME / "pronounce.txt", 2), "idioms": (config.APP_HOME / "idioms.txt", 3)}
_lexicon_lock = threading.Lock()


def _lexicon(kind: str):
    if kind not in LEXICONS:
        raise HTTPException(404, "فهرست ناشناخته")
    return LEXICONS[kind]


def _lexicon_lines(path):
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _code_copies(kind: str):
    """Where a saved fix is also written so it lives in the code: the installed engine,
    the source copy install.sh keeps, and the folder install.sh was run from."""
    name = f"{kind}.txt"
    out = [Path(__file__).parent / "words" / name,
           config.APP_HOME / "source" / "engine" / "hamzaban" / "words" / name]
    try:
        src = Path((config.APP_HOME / "source_dir.txt").read_text(encoding="utf-8").strip())
        if src.is_dir():
            out.append(src / "engine" / "hamzaban" / "words" / name)
    except OSError:
        pass
    return list(dict.fromkeys(out))


def _save_lexicon(kind: str, lines: list[str]):
    path, _ = LEXICONS[kind]
    text = "\n".join(lines).strip("\n") + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    entries = [ln for ln in lines if _entry_key(ln) is not None]
    head = ("# Fixes saved from the ✎ panel / the Mac app (also in ~/Library/Application Support/Hamzaban/"
            f"{path.name}). Kept in the code so they survive reinstalls.\n")
    for copy in _code_copies(kind):
        try:
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_text(head + "\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")
        except OSError:
            pass
    # sentences already prepared but not heard yet are said / translated again
    with engine.jobs_lock:
        jobs = [j for j in engine.jobs.values() if not j.stopped]
    for job in jobs:
        job.refresh_words()


def _entry_key(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    return line.split("=", 1)[0].strip().lower()


def _parsigo(voice: str) -> bool:
    from . import aava, omni

    return bool(voice) and not (omni.is_omni(voice) or aava.is_aava(voice) or voice == "clone")


@app.get("/api/spoken")
def spoken(text: str = "", voice: str = ""):
    """What the voice is given for this text now (after pronunciations, numbers, English);
    for a ParSiGo voice also the exact phonemes it will say."""
    from .speech import for_voice

    said = for_voice(text[:300], phonetic=_parsigo(voice))
    out = {"spoken": said}
    if _parsigo(voice):
        out["phonemes"] = engine.tts.phonemes(said)
    return out


@app.get("/api/lexicon/{kind}")
def lexicon_list(kind: str):
    path, n = _lexicon(kind)
    out = []
    for line in _lexicon_lines(path):
        if _entry_key(line) is not None:
            parts = [x.strip() for x in line.split("=")]
            out.append((parts + [""] * n)[:n])
    return {"entries": out}


@app.post("/api/lexicon/{kind}")
def lexicon_add(kind: str, body: dict):
    """{parts: [...]}: adds the entry, replacing one with the same first part."""
    path, n = _lexicon(kind)
    parts = [" ".join(str(x or "").split())[:200] for x in (body.get("parts") or [])][:n]
    parts += [""] * (n - len(parts))
    if any("=" in x or "#" in x for x in parts) or not parts[0] or not any(parts[1:]):
        raise HTTPException(400, "واژه و جایگزینش را بنویسید (بدون = و #)")
    if kind == "pronounce":
        from .pronounce import HOMOGRAPHS, stem

        parts[0] = stem(parts[0])
        if parts[0] in HOMOGRAPHS:
            raise HTTPException(400, f"«{parts[0]}» دو تلفظ دارد و هم‌زبان خودش از روی جمله‌ی انگلیسی درستش را انتخاب می‌کند؛ "
                                     "یک قاعده‌ی همیشگی معنای دیگرش را خراب می‌کند.")
        same = lambda x: x.replace("\u200c", "").replace(" ", "").lower()
        if same(parts[0]) == same(stem(parts[1])):
            raise HTTPException(400, "تلفظ با خود کلمه یکی است؛ آن را طوری بنویسید که باید گفته شود (مثلاً با اعراب: کُد).")
    if kind == "idioms":
        parts[0] = re.escape(parts[0].strip(" .,!?;:")).replace("\\ ", " ")  # typed text, not a pattern
    with _lexicon_lock:
        key = parts[0].lower()
        lines = [ln for ln in _lexicon_lines(path) if _entry_key(ln) != key]
        lines.append(" = ".join(parts).rstrip(" ="))
        _save_lexicon(kind, lines)
    return lexicon_list(kind)


@app.post("/api/lexicon/{kind}/delete")
def lexicon_delete(kind: str, body: dict):
    path, _ = _lexicon(kind)
    key = str(body.get("key") or "").strip().lower()
    with _lexicon_lock:
        lines = _lexicon_lines(path)
        kept = [ln for ln in lines if _entry_key(ln) != key]
        if len(kept) != len(lines):
            _save_lexicon(kind, kept)
    return lexicon_list(kind)


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    clean_old_dub_dirs(engine.cache_dir)
    persian.normalize("سلام")  # load the persian-writing word list before the first request
    threading.Thread(target=engine.reap, daemon=True, name="reaper").start()
    uvicorn.run(app, host=cfg["host"], port=int(cfg["port"]), log_level="warning")
