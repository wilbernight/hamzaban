"""Link -> synchronized Persian dub, prepared ahead of the playhead.

Three workers form a pipeline so the stages overlap:

    ASR  (Whisper, GPU)   cuts the audio into windows starting at the
                          playhead and turns speech into timed sentences
    MT   (TranslateGemma) translates each window's sentences together
    TTS  (ParSiGo, CPU)   speaks every sentence, sped up (pitch kept) when
                          the Persian runs longer than the original slot

Every worker always picks the unfinished item closest to the playhead, so
after a seek the new position is served first. The player only asks for
finished sentences; "ready_until" tells it how far ahead the dub is ready,
which lets it pause the video instead of drifting out of sync.

When macOS reports the Mac as hot, ASR and TTS prepare less far ahead and
leave the GPU idle between batches (see thermal.py), so the fans and the
chip get a break while the dub still stays ahead of playback.
"""
import logging
import threading
import time
import uuid

from . import asr as asr_mod
from . import thermal
from .media import AudioSource, LiveSource, MediaError
from .pronounce import in_context
from .pronounce import signature as pronounce_signature
from .spoken import colloquial
from .speech import for_voice
from .tts import to_wav_bytes
from .translator import TranslationError

log = logging.getLogger("hamzaban.dub")

FIRST_WINDOW = 12.0   # short first window = fast start
WINDOW = 30.0
OVERLAP = 5.0         # read past the window so the last sentence is not cut
LOOKAHEAD = 120.0     # never prepare more than this ahead of the playhead
HOT_LOOKAHEAD = 45.0  # ...and only this much while macOS reports the Mac as hot
HOT_MIN_LEAD = 20.0   # on a hot Mac, rest the GPU between batches while this much is ready
ASR_AHEAD = 45.0      # ...nor transcribe further than this beyond the finished dub (GPU goes to TTS first)
MAX_BATCH = 12
SLOT_MARGIN = 0.25    # seconds of breathing room before the next sentence
MAX_SPEEDUP = 1.3     # time stretch at most this much (keeps every word); longer clips run
                      # over and the player slows the video a little until the dub catches up
LEAD_IN = 6.0         # a dub starting mid-sentence transcribes from a little earlier, so that
                      # sentence is caught whole (the player rewinds to where it begins)
IDLE_STOP = 600.0     # stop a job nobody polled for 10 minutes
# live mode (audio captured from the playing video): short windows, so the dub
# follows the picture by a few seconds
LIVE_MIN = 3.0        # transcribe once this much new audio is in (subtitles lag ~3-4 s)
LIVE_MAX = 10.0       # ...and cut here even mid-sentence
LIVE_BACK = 20.0      # never start further back than this behind the playhead
LIVE_STALE = 30.0     # sentences this far behind the playhead are not spoken any more
LIVE_EDGE = 1.2       # a sentence must end this long before the sound so far ends to count as finished

STATUS_FA = {
    "resolving": "در حال پیدا کردن صدای ویدیو…",
    "running": "دوبله فعال است",
    "error": "خطا",
    "stopped": "متوقف شد",
}


class Sentence:
    __slots__ = ("id", "start", "end", "src", "fa", "state", "dur", "seq", "region", "tseq", "rev", "said", "asked")

    def __init__(self, sid, start, end, src, region):
        self.id, self.start, self.end, self.src, self.region = sid, start, end, src, region
        self.fa, self.state, self.dur, self.seq, self.tseq = "", "tr", 0.0, 0, 0
        self.rev = 0        # bumped when a saved fix changes it: the player swaps the audio in
        self.said = None    # what the voice was given for it
        self.asked = None   # what the translation model was given for it

    def public(self):
        return {"id": self.id, "start": round(self.start, 3), "end": round(self.end, 3),
                "fa": self.fa, "src": self.src, "dur": round(self.dur, 3),
                "audio": self.dur > 0, "rev": self.rev}


class Region:
    __slots__ = ("a", "b", "sids")

    def __init__(self, a, b):
        self.a, self.b, self.sids = a, b, []


def _asked(src, lang):
    """What the translation model is given for an English sentence (idioms resolved)."""
    from . import idioms

    if lang != "en":
        return src
    return idioms.whole(src) or idioms.plain(src)


def covered_until(regions, t, eps=0.05):
    """End of the run of back-to-back regions that contains t (t if none)."""
    x = t
    for r in sorted(regions, key=lambda r: r.a):
        if r.a <= x + eps and r.b > x:
            x = r.b
    return x


def next_window(start, seg_list, win, got, at_end):
    """Pick which transcribed segments of a window to keep and where the next
    window starts. Segments that run into the unread tail are dropped: the
    next window starts at the end of the last kept one and hears them whole."""
    hard_end = win + OVERLAP
    if at_end:
        return list(seg_list), start + got
    keep = [s for s in seg_list if s["start"] < win and s["end"] <= min(got, hard_end) - 0.3]
    if keep:
        return keep, start + keep[-1]["end"]
    if seg_list and seg_list[0]["start"] > 1.0:
        return [], start + seg_list[0]["start"]
    return [], start + win


class DubJob:
    def __init__(self, engine, page_url, media_url, voice, t0, model, captions=None, speak=True, cookies=None,
                 can_live=False, style="spoken"):
        self.id = uuid.uuid4().hex[:12]
        self.engine = engine
        self.page_url = page_url
        self.voice = voice
        self.model = model
        # {"cues": [{start, end, text}], "duration": s, "lang": "en"} from the page
        # itself (Udemy, <track> subtitles): no audio or Whisper needed, works with DRM
        self.captions = captions
        self.speak = speak  # False: subtitles only, no Persian speech
        self.dir = engine.cache_dir / "dub" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.source = AudioSource(page_url, media_url, self.dir, cookies)
        self.can_live = can_live  # the extension can send the audio itself if no link works
        self.style = style        # "spoken": the voice talks the way people do; "written": reads the text
        self.live = False
        self.status, self.error = "resolving", None
        self.lang = None
        self.clone_path = None
        self.clone_text = None
        self.playhead = max(0.0, float(t0 or 0))
        self.regions: list[Region] = []
        self.sents: dict[int, Sentence] = {}
        self.seq = 0
        self.tseq = 0  # live: translations numbered as they finish (subtitles before the voice)
        self._sid = 0
        self.cv = threading.Condition()
        self.stopped = False
        self.last_poll = time.time()
        self.created = time.time()

    # ------------------------------------------------------------ control
    def start(self):
        threading.Thread(target=self._boot, daemon=True, name=f"dub-{self.id}").start()

    def stop(self):
        with self.cv:
            self.stopped = True
            if self.status != "error":
                self.status = "stopped"
            self.cv.notify_all()

    def _fail(self, msg):
        log.error("job %s: %s", self.id, msg)
        with self.cv:
            self.status, self.error, self.stopped = "error", msg, True
            self.cv.notify_all()

    def _boot(self):
        if self.captions:
            self._load_captions()
            workers = [self._mt_loop]
        else:
            try:
                self.source.resolve()
                workers = [self._asr_loop, self._mt_loop]
            except Exception as e:
                if not self.can_live:
                    return self._fail(str(e))
                # no link the engine can read (a player inside a frame, a blob stream):
                # the extension sends the sound of the video as it plays instead
                log.info("job %s: live mode (%s)", self.id, str(e)[:120])
                self.source, self.live = LiveSource(), True
                workers = [self._live_asr_loop, self._mt_loop]
        if self.speak:
            workers.append(self._tts_loop)
        with self.cv:
            self.status = "running"
        for fn in workers:
            threading.Thread(target=self._guard(fn), daemon=True,
                             name=f"{fn.__name__}-{self.id}").start()

    def _guard(self, fn):
        def run():
            try:
                fn()
            except Exception as e:  # keep the reason visible in the player
                log.exception("worker crashed")
                self._fail(f"{type(e).__name__}: {e}")
        return run

    # ------------------------------------------------------------ helpers
    def _priority(self, t):
        """Ahead of the playhead first (nearest first), passed items last.
        Live: everything is behind the playhead; oldest first, as it is spoken."""
        if self.live:
            return (0, t)
        return (0, t - self.playhead) if t >= self.playhead - 1.0 else (1, self.playhead - t)

    @property
    def duration(self):
        return self.source.duration

    def _at_media_end(self, x):
        return self.duration is not None and x >= self.duration - 0.5

    def lookahead(self):
        """How far ahead of the playhead to prepare. Less on a hot Mac: the dub still
        stays ahead of playback, but the GPU gets long pauses to cool down."""
        return HOT_LOOKAHEAD if thermal.hot() else LOOKAHEAD

    def _rest_if_hot(self, worked):
        """On a hot Mac, leave the GPU idle as long as the last batch kept it busy
        (half the power), as long as the dub is comfortably ahead of playback."""
        if worked <= 0 or not thermal.hot():
            return
        until = time.monotonic() + min(worked, 30.0)
        with self.cv:
            while (not self.stopped and time.monotonic() < until
                   and self.ready_until() - self.playhead > HOT_MIN_LEAD):
                self.cv.wait(timeout=min(1.0, max(0.05, until - time.monotonic())))

    def ready_until(self):
        x = self.playhead
        for r in sorted(self.regions, key=lambda r: r.a):
            if r.a <= x + 0.05 and r.b > x:
                pending = [self.sents[i].start for i in r.sids if self.sents[i].state != "ready"]
                if pending:
                    return max(self.playhead, min(pending))
                x = r.b
        return x

    # ------------------------------------------------------------ ASR
    def _asr_loop(self):
        asr = self.engine.asr
        cache = self.engine.asr_cache
        known = cache.load(self.source.key)  # what an earlier dub of this video already heard
        sr = asr_mod.SAMPLE_RATE
        while True:
            with self.cv:
                while True:
                    if self.stopped:
                        return
                    start = covered_until(self.regions, self.playhead)
                    if start <= self.playhead:  # nothing prepared here yet: back up a little
                        before = max((r.b for r in self.regions if r.b <= self.playhead), default=0.0)
                        start = max(before, self.playhead - LEAD_IN)
                    # transcribing far ahead competes with speech synthesis for the GPU and
                    # makes the part being watched wait; stay a little ahead of the finished dub
                    ahead_of_dub = start - max(self.ready_until(), self.playhead)
                    if (not self._at_media_end(start) and start - self.playhead < self.lookahead()
                            and ahead_of_dub < ASR_AHEAD):
                        break
                    self.cv.wait(timeout=1.0)
                if self._use_cached(known, start):
                    continue
                win = FIRST_WINDOW if start - self.playhead < 5.0 else WINDOW
                win += max(0.0, self.playhead - start)  # the lead-in comes on top
                if self.duration:
                    win = min(win, max(1.0, self.duration - start))
            began = time.monotonic()
            audio = self._read_audio(start, win + OVERLAP, sr)
            got = len(audio) / sr
            at_end = got < win + OVERLAP - 0.5
            segs, lang = asr.transcribe(audio, self.lang) if got > 0.3 else ([], self.lang)
            keep, end = next_window(start, segs, win, got, at_end)
            if at_end and self.source.duration is None:
                self.source.duration = start + got
            if self.voice == "clone" and self.clone_path is None:
                self._make_clone_prompt(audio, keep, sr)
            # ASR already cuts at sentence ends; only glue fragments with no pause between them
            sentences = asr_mod.to_sentences(keep, max_gap=0.3)
            with self.cv:
                if self.stopped:
                    return
                if lang and not self.lang and sentences:
                    self.lang = lang
                region = Region(start, max(end, start + 0.5))
                for s in sentences:
                    self._add_sentence(start + s["start"], start + s["end"], s["text"], region)
                self.regions.append(region)
                self.cv.notify_all()
            cache.add(self.source.key, {
                "a": region.a, "b": region.b, "lang": lang if sentences else None,
                "sents": [{"start": round(start + s["start"], 3), "end": round(start + s["end"], 3),
                           "text": s["text"]} for s in sentences]}, known)
            log.info("asr %.1f-%.1f: %d sentences (%s)", start, region.b, len(sentences), self.lang)
            # fetch the whole track only now, so it does not slow the first window down
            self.source.start_background_download()
            self._rest_if_hot(time.monotonic() - began)

    def _live_asr_loop(self):
        """Whisper over the audio the extension sends: from the first moment not yet
        transcribed (near the playhead), as soon as LIVE_MIN seconds are in; only
        whole sentences are kept unless LIVE_MAX seconds pass without a pause."""
        import time as _t

        asr = self.engine.asr
        src = self.source
        tried = None  # (start, audio end) of the last attempt that kept nothing
        while True:
            with self.cv:
                if self.stopped:
                    return
                playhead = self.playhead
                regions = list(self.regions)
            start = end = None
            for a, b in src.spans():
                x = covered_until(regions, max(a, playhead - LIVE_BACK))
                if x < b - 0.5:
                    start, end = x, b
                    break
            quiet = _t.monotonic() - src.fed_at > 2.5  # paused or ended: use what is there
            if start is None or (end - start < LIVE_MIN and not (quiet and end - start >= 1.0)) \
                    or (tried and tried[0] == start and end - tried[1] < 2.0 and not quiet):
                src.wait(0.25)
                continue
            rate = src.rate_at(start)
            audio = src.read(start, min(end - start, LIVE_MAX * rate))
            got = len(audio) / asr_mod.SAMPLE_RATE  # seconds of sound (video seconds = got * rate)
            peak = float(abs(audio).max()) if len(audio) else 0.0
            if 0.0 < peak < 0.5:  # the original may be turned down under the dub
                audio = audio * (0.5 / peak)
            with self.cv:  # the last sentences heard: context, so the window edge is not taken for a full stop
                said = " ".join(x.src for x in sorted(self.sents.values(), key=lambda x: x.start)[-2:])
            segs, lang = asr.transcribe(audio, self.lang, prompt=said[-200:] or None)
            forced = got >= LIVE_MAX - 0.5 or quiet
            # Whisper ends every window with a full stop; a sentence ending this close to the
            # edge may go on in the next sound, so it waits (fewer half-sentences)
            done_at = got - LIVE_EDGE
            pending = [x for x in segs if x["end"] > done_at]
            if forced:
                keep, upto = segs, got
            elif pending:   # hear again only from where the unfinished sentence starts
                keep, upto = [x for x in segs if x["end"] <= done_at], pending[0]["start"]
            else:           # all heard: music or silence after the last sentence is skipped too
                keep, upto = segs, (done_at if segs else got - 0.5)
            if not keep and upto < 0.8:  # a sentence has only just begun: wait for more sound
                tried = (start, end)
                continue
            tried = None
            sentences = asr_mod.to_sentences(keep, max_gap=0.3)
            stop = start + upto * rate
            with self.cv:
                if self.stopped:
                    return
                if lang and not self.lang and sentences:
                    self.lang = lang
                region = Region(start, max(stop, start + 0.5))
                for x in sentences:
                    self._add_sentence(start + x["start"] * rate, start + x["end"] * rate, x["text"], region)
                self.regions.append(region)
                self.cv.notify_all()
            dropped = getattr(asr, "last_dropped", None)
            log.info("live asr %.1f-%.1f: %d sentences (%s)%s", start, region.b, len(sentences), self.lang,
                     f"; dropped as noise: {dropped}" if dropped else "")

    def _add_sentence(self, start, end, text, region):
        self._sid += 1
        sent = Sentence(self._sid, start, end, text, region)
        self.sents[sent.id] = sent
        region.sids.append(sent.id)

    def _use_cached(self, known, start):
        """Take the stretch from start on from an earlier dub of this video instead
        of listening again (called with self.cv held). Cut to fit between the
        regions this job already has, so no sentence is dubbed twice."""
        if not known or (self.voice == "clone" and self.clone_path is None):
            return False  # the clone voice needs a sentence of the original audio first
        hit = max((r for r in known if r["a"] <= start + 0.05 and r["b"] > start + 0.5),
                  key=lambda r: r["b"], default=None)
        if hit is None:
            return False
        lo = max([hit["a"]] + [r.b for r in self.regions if r.b <= start + 0.05])
        hi = min([hit["b"]] + [r.a for r in self.regions if r.a > start + 0.05])
        if hi - start < 0.5:
            return False
        region = Region(lo, hi)
        for s in hit["sents"]:
            if lo - 0.3 <= s["start"] < hi - 0.05:
                self._add_sentence(s["start"], s["end"], s["text"], region)
        if hit.get("lang") and not self.lang:
            self.lang = hit["lang"]
        self.regions.append(region)
        self.cv.notify_all()
        log.info("asr %.1f-%.1f: %d sentences (from cache)", lo, hi, len(region.sids))
        return True

    def _load_captions(self):
        """Subtitle cues -> sentences, laid out as back-to-back ~30 s regions
        (the same shape the ASR worker produces), all at once."""
        cues = sorted(({"start": float(c["start"]), "end": float(c["end"]), "text": " ".join(str(c["text"]).split())}
                       for c in self.captions.get("cues", []) if str(c.get("text", "")).strip()),
                      key=lambda c: c["start"])
        sents = asr_mod.to_sentences(cues, max_len=12.0, max_gap=1.0)
        end = float(self.captions.get("duration") or 0) or (sents[-1]["end"] + 1.0 if sents else 1.0)
        self.source.title = self.captions.get("title") or ""
        self.source.duration = end
        lang = self.captions.get("lang")
        if not lang and sents:
            from .langs import detect

            lang = detect(" ".join(s["text"] for s in sents[:40]))
        with self.cv:
            self.lang = lang or "en"
            region = Region(0.0, 0.0)
            for s in sents:
                if region.sids and s["start"] - region.a > WINDOW:
                    region.b = s["start"]
                    self.regions.append(region)
                    region = Region(s["start"], s["start"])
                self._sid += 1
                sent = Sentence(self._sid, s["start"], s["end"], s["text"], region)
                self.sents[sent.id] = sent
                region.sids.append(sent.id)
            region.b = max(end, region.a + 0.5)
            self.regions.append(region)
        log.info("captions: %d cues -> %d sentences (%s), %.0fs", len(cues), len(sents), self.lang, end)

    def _make_clone_prompt(self, audio, segs, sr):
        """One whole sentence of 2.5-9 s as the voice prompt, with its words
        (OmniVoice needs the transcript; ParSiGo uses the first 5 s)."""
        for s in segs:
            if 2.5 <= s["end"] - s["start"] <= 9.0:
                a = audio[int(s["start"] * sr): int(s["end"] * sr)]
                path = self.dir / "clone.wav"
                path.write_bytes(to_wav_bytes(a, sr))
                self.clone_path, self.clone_text = path, s["text"]
                return

    def _read_audio(self, start, seconds, sr):
        """Network reads can hiccup; retry before giving up on the job."""
        for attempt in range(3):
            try:
                return self.source.read(start, seconds, sr)
            except MediaError:
                if attempt == 2:
                    raise
                time.sleep(2.0 * (attempt + 1))

    # ------------------------------------------------------------ MT
    def _mt_loop(self):
        tr = self.engine.translator
        model = self.model
        while True:
            with self.cv:
                while True:
                    if self.stopped:
                        return
                    todo = [s for s in self.sents.values() if s.state == "tr"]
                    if todo:
                        first = min(todo, key=lambda s: self._priority(s.start))
                        batch = sorted((s for s in todo if s.region is first.region),
                                       key=lambda s: s.start)[:MAX_BATCH]
                        for s in batch:
                            s.state = "mt"
                        break
                    self.cv.wait(timeout=1.0)
                lang = self.lang or "en"
            for s in batch:
                s.asked = _asked(s.src, lang)
            try:
                fa = tr.translate_lines([s.src for s in batch], model, lang)
            except TranslationError as e:
                # one bad sentence («token repeat limit reached») must not end the dub: try
                # them one by one, give up only on those that fail; the engine being gone
                # (Ollama off) is still an error the player shows
                if "Ollama" in str(e) and "روشن" in str(e):
                    return self._fail(str(e))
                log.warning("translation failed for %d sentences, one by one: %s", len(batch), e)
                fa = []
                for s in batch:
                    try:
                        fa.append(tr.translate_lines([s.src], model, lang)[0])
                    except TranslationError:
                        fa.append("")
            with self.cv:
                for s, text in zip(batch, fa):
                    s.fa = text
                    if self.live:
                        self.tseq += 1
                        s.tseq = self.tseq
                    if self.speak:
                        s.state = "tts"
                    else:  # subtitles only: done as soon as it is translated
                        self.seq += 1
                        s.seq, s.state = self.seq, "ready"
                self.cv.notify_all()

    # ------------------------------------------------------------ TTS
    def _slot(self, s):
        later = [o.start for o in self.sents.values() if o.start > s.start + 0.05]
        nxt = min(later) if later else s.end + 1.5
        return max(nxt - s.start - 0.1, s.end - s.start, 0.8)

    def _tts_loop(self):
        tts = self.engine.tts
        cache = self.engine.tts_cache
        while True:
            with self.cv:
                while True:
                    if self.stopped:
                        return
                    todo = sorted((s for s in self.sents.values() if s.state == "tts"),
                                  key=lambda s: self._priority(s.start))
                    if self.live:  # long passed (a seek, a slow Mac): not worth saying any more
                        for s in [s for s in todo if s.end < self.playhead - LIVE_STALE]:
                            self.seq += 1
                            s.seq, s.state, s.said = self.seq, "ready", "stale"  # see poll: a seek back
                        todo = [s for s in todo if s.state == "tts"]
                    if todo:
                        # batching voices (Aava): a small batch while the playhead is close
                        # (first sound sooner), a full one while there is time in hand
                        n = tts.batch_size(self.voice)
                        if n > 1 and todo[0].start - self.playhead < 8.0:
                            n = 2
                        batch = todo[:n]
                        for s in batch:
                            s.state = "speaking"
                        slots = [self._slot(s) for s in batch]
                        break
                    self.cv.wait(timeout=1.0)
            voice, ref = self.voice, None
            if voice == "clone":  # the original speaker's voice, once a clean sentence was found
                if self.clone_path:
                    ref = (self.clone_path, self.clone_text or "")
                else:
                    voice = self.engine.cfg["default_voice"]
            # spoken before (same voice, text and slot: another dub of this video)? reuse it
            for s in batch:
                s.said = self._said(s)
            todo = []
            for s, slot in zip(batch, slots):
                # keyed by what the voice is actually given (and its settings), so a better
                # pronunciation rule or voice setting never replays an old clip
                key = None if ref else cache.key(f"{voice}|{self._voice_settings()}",
                                                 self._said(s), slot - SLOT_MARGIN)
                dur = cache.fetch(key, self.dir / f"{s.id}.wav") if key and s.fa else None
                if dur is None:
                    todo.append((s, slot, key))
                else:
                    self._finish(s, dur)
            if not todo:
                continue
            began = time.monotonic()
            audios = [None] * len(todo)
            for attempt in range(2):  # a failed sentence would be silent in the dub: try once more
                try:
                    audios = tts.synth_many([self._speech(s) for s, _, _ in todo], voice,
                                            slots=[sl - SLOT_MARGIN for _, sl, _ in todo], ref=ref)
                    break
                except Exception:
                    log.exception("tts failed for %d sentences (attempt %d)", len(todo), attempt + 1)
            for (s, slot, key), audio in zip(todo, audios):
                dur = 0.0
                if s.fa and audio is not None and len(audio):
                    try:
                        # end a little before the next sentence: the player absorbs small delays there
                        audio = tts.fit(audio, slot - SLOT_MARGIN, max_speedup=MAX_SPEEDUP)
                        path = self.dir / f"{s.id}.wav"
                        path.write_bytes(to_wav_bytes(audio, tts.sample_rate))
                        dur = len(audio) / tts.sample_rate
                        if key:
                            cache.store(key, path)
                    except Exception:
                        log.exception("tts failed for %r", s.fa[:80])
                self._finish(s, dur)
            self._rest_if_hot(time.monotonic() - began)

    def refresh_words(self):
        """The user just saved a fix: sentences prepared but not heard yet whose
        translation or speech it changes are done again (the player swaps them in)."""
        with self.cv:
            for s in self.sents.values():
                if s.state != "ready" or s.end < self.playhead - 0.5:
                    continue
                if s.asked is not None and _asked(s.src, self.lang or "en") != s.asked:
                    s.state, s.rev = "tr", s.rev + 1
                elif self.speak and s.said is not None and s.fa and self._said(s) != s.said:
                    s.state, s.rev = "tts", s.rev + 1
            self.cv.notify_all()

    def _said(self, s):
        """Everything the sound of a sentence depends on: the text the voice gets, and
        (a ParSiGo voice) the user's phoneme fixes."""
        from . import aava, omni

        parsigo = not (omni.is_omni(self.voice) or aava.is_aava(self.voice) or self.voice == "clone")
        return for_voice(self._speech(s), phonetic=parsigo) + "|" + pronounce_signature()

    def _speech(self, s):
        """What the voice says for a sentence: two-reading words («اشکال», «کشتی»…) read
        as the English says, and in spoken Persian unless the user chose written."""
        text = in_context(s.fa, s.src)
        return colloquial(text) if self.style == "spoken" else text

    def _voice_settings(self):
        c = self.engine.cfg
        return f"{c.get('omni_steps')}/{c.get('omni_qbits')}/{c.get('omni_codec_dtype')}"

    def _finish(self, s, dur):
        with self.cv:
            self.seq += 1
            s.dur, s.seq, s.state = dur, self.seq, "ready"
            self.cv.notify_all()

    # ------------------------------------------------------------ API
    def poll(self, t=None, since=0, tsince=None):
        with self.cv:
            self.last_poll = time.time()
            if t is not None:
                new = max(0.0, float(t))
                if self.live and self.speak and new < self.playhead - 5.0:
                    # a seek back: sentences once skipped as too old are coming up again
                    for s in self.sents.values():
                        if s.said == "stale" and s.fa and s.end > new and s.start < new + LOOKAHEAD:
                            s.state, s.said, s.rev = "tts", None, s.rev + 1
                self.playhead = new
                self.cv.notify_all()
            ready = sorted((s for s in self.sents.values() if s.state == "ready" and s.seq > since),
                           key=lambda s: s.seq)
            ru = self.ready_until()
            return {
                "id": self.id, "status": self.status, "status_fa": STATUS_FA.get(self.status, ""),
                "error": self.error, "title": self.source.title, "duration": self.duration,
                "lang": self.lang, "voice": self.voice,
                "ready_until": round(ru, 3), "eof": self._at_media_end(ru),
                # the player never waits for more than this (the engine stops preparing there)
                "lead_cap": self.lookahead(), "hot": thermal.hot(),
                "seq": self.seq, "segments": [s.public() for s in ready], "live": self.live,
                # live: subtitles as soon as they are translated, before the voice is ready
                **({"tseq": self.tseq, "texts": [
                    {"id": s.id, "start": round(s.start, 3), "end": round(s.end, 3), "fa": s.fa, "src": s.src}
                    for s in sorted(self.sents.values(), key=lambda s: s.tseq)
                    if s.tseq > tsince and s.fa]} if self.live and tsince is not None else {}),
            }

    def audio_path(self, sid: int):
        p = self.dir / f"{sid}.wav"
        return p if p.exists() else None
