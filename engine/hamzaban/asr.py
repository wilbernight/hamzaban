"""Speech recognition with Whisper large-v3-turbo on Apple Silicon (MLX)."""
import re
from concurrent.futures import ThreadPoolExecutor

SAMPLE_RATE = 16000

# Whisper's classic hallucinations on music / silence. The "suspect" ones are
# also said for real, so they are dropped only when Whisper doubts the speech.
_JUNK = re.compile(
    r"(subtitles? by|amara\.org|^\W*(music|applause|laughter)\W*$|^[\W_]+$|♪)", re.I)
_SUSPECT = re.compile(r"(thank(s| you) for watching|subscribe)", re.I)
# On music and noise Whisper invents short words ("you", "So") with very low word
# confidence (0.03 measured), while real speech averages 0.97 per sentence (lowest
# 0.54, a one-word sentence). A sentence is dropped below these mean confidences.
MIN_PROB = 0.4
MIN_PROB_SHORT = 0.5      # one or two words
MIN_PROB_HALLU = 0.6      # the words Whisper invents most
_HALLU = re.compile(r"^\W*(you|thank you|thanks|bye|so|okay|oh|uh|um|hmm|yeah|the end)\W*$", re.I)


def believable(sentence) -> bool:
    """Real speech, not Whisper hearing words in music or noise."""
    p = sentence.get("prob")
    if p is None:
        return True
    n = len(sentence["text"].split())
    if _HALLU.match(sentence["text"]):
        return p >= MIN_PROB_HALLU
    return p >= (MIN_PROB_SHORT if n <= 2 else MIN_PROB)


class Transcriber:
    def __init__(self, model_repo: str, cache_mb: int = 256):
        self.repo = model_repo
        # MLX streams are per thread: load and run the model on one thread only
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-whisper")
        if cache_mb >= 0:  # give freed GPU buffers back instead of hoarding them
            self.pool.submit(self._limit_cache, cache_mb).result()

    @staticmethod
    def _limit_cache(mb):
        import mlx.core as mx

        mx.set_cache_limit(mb * 1024 * 1024)

    def _run(self, audio, language, prompt=None):
        import mlx_whisper

        return mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.repo, language=language, task="transcribe",
            condition_on_previous_text=False, temperature=(0.0, 0.2, 0.4),
            compression_ratio_threshold=2.4, logprob_threshold=-1.0,
            no_speech_threshold=0.6, word_timestamps=True, verbose=None,
            initial_prompt=prompt or None)

    def transcribe(self, audio, language: str | None = None, prompt: str | None = None):
        """16 kHz mono float32 -> ([{start, end, text}] sentence by sentence, language).

        Whisper's own segments often end mid-sentence; its word timings let
        the dub be cut exactly where sentences end."""
        # prompt: what was said just before (live windows), so a window's edge is not
        # taken for the end of a sentence
        res = self.pool.submit(self._run, audio, language, prompt).result()
        words, loose = [], []
        for s in res.get("segments", []):
            text = s.get("text", "").strip()
            no_speech = s.get("no_speech_prob", 0)
            if not text or _JUNK.search(text) or (_SUSPECT.search(text) and no_speech > 0.3):
                continue
            if no_speech > 0.6 and s.get("avg_logprob", 0) < -0.8:
                continue
            if s.get("words"):
                words += [{"start": float(w["start"]), "end": float(w["end"]), "word": w["word"],
                           "p": float(w.get("probability", 1.0))} for w in s["words"]]
            else:
                loose.append({"start": float(s["start"]), "end": float(s["end"]), "text": text})
        found = words_to_sentences(words)
        # kept for the log: what was taken for music or noise («you» p=0.12)
        self.last_dropped = [f"{x['text'][:40]!r} p={x.get('prob', 0):.2f}" for x in found if not believable(x)][:4]
        sents = sorted([x for x in found if believable(x)] + loose, key=lambda s: s["start"])
        return sents, res.get("language") or language


_END = re.compile(r"[.!?。！？…؟]['\"»”’)]*$")
_SOFT_END = re.compile(r"[,،;:，、；：]['\"»”’)]*$")
_ABBREV = {"mr.", "mrs.", "ms.", "dr.", "st.", "vs.", "e.g.", "i.e.", "u.s.", "no.", "prof.", "jr.", "sr."}


def words_to_sentences(words, max_len=12.0, hard_len=16.0, max_gap=0.8):
    """Group timed words into sentences: cut at sentence punctuation, at a
    pause, or (for run-on speech) at a comma once the sentence is long."""
    out, cur = [], []

    def flush():
        if cur:
            text = "".join(w["word"] for w in cur).strip()
            if text:
                sent = {"start": cur[0]["start"], "end": cur[-1]["end"], "text": text}
                if all("p" in w for w in cur):
                    sent["prob"] = sum(w["p"] for w in cur) / len(cur)
                out.append(sent)
            cur.clear()

    for w in words:
        if cur and w["start"] - cur[-1]["end"] > max_gap:
            flush()
        cur.append(w)
        token = w["word"].strip()
        length = cur[-1]["end"] - cur[0]["start"]
        if ((_END.search(token) and token.lower() not in _ABBREV)
                or (length > max_len and _SOFT_END.search(token)) or length > hard_len):
            flush()
    flush()
    return out


def to_sentences(segs, max_len=12.0, max_gap=1.0):
    """Merge Whisper segments into sentence-sized dubbing units."""
    out = []
    for s in segs:
        if out:
            last = out[-1]
            joinable = (not _END.search(last["text"])
                        and s["start"] - last["end"] <= max_gap
                        and s["end"] - last["start"] <= max_len)
            if joinable:
                last["text"] = f"{last['text']} {s['text']}"
                last["end"] = s["end"]
                continue
        out.append(dict(s))
    return out
