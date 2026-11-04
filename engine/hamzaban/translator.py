"""Any-language -> Persian translation with TranslateGemma running in Ollama.

Text is cut into paragraph-aligned pieces (the model is most faithful on a
few paragraphs at a time), each piece is translated with the exact prompt
TranslateGemma was trained on, and the result goes through a conservative
Persian clean-up. Pieces that need no translation (links, numbers, text that
is already Persian) pass through untouched. Raw model output is cached on
disk (cleaned on the way out, so better clean-up rules apply to old
translations too), and re-opening an e-mail is instant.
"""
import hashlib
import json
import re
import sqlite3
import threading

from . import glossary, idioms, langs, persian

MAX_PIECE_CHARS = 1400
TARGET = ("Persian", "fa-IR")
OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 2048}  # greedy, as in the model card
# Dub requests are a dozen short lines; a smaller context means a smaller KV cache in
# memory. Fixed per model (not per request), since Ollama reloads a model whose
# num_ctx changes.
SMALL_CTX = 3072

_LETTER = re.compile(r"[^\W\d_]")
_URLISH = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SENTENCE_END = re.compile(r"(?<=[.!?。！？])\s+")


class TranslationError(RuntimeError):
    """A problem the user can act on; the message is shown in the UI (Persian)."""


def prompt_for(text: str, src: str) -> str:
    sl, (tl, tc) = langs.name_en(src), TARGET
    return (
        f"You are a professional {sl} ({src}) to {tl} ({tc}) translator. Your goal is to "
        f"accurately convey the meaning and nuances of the original {sl} text while adhering "
        f"to {tl} grammar, vocabulary, and cultural sensitivities.\n"
        f"Produce only the {tl} translation, without any additional explanations or "
        f"commentary. Please translate the following {sl} text into {tl}:\n\n\n{text}"
    )


# The model sometimes offers alternatives or a gloss a subtitle has no room for:
# «کمک! (یا: درخواست کمک!)», «"کمک! کمک!" (یا "در خطر! در خطر!")», «کمک! کمک! (Mayday)».
_ALTERNATIVE = re.compile(r"\s*[(（]\s*(?:یا|or)\b[^()（）]*[)）]")
_LATIN_GLOSS = re.compile(r"\s*[(（][^()（）\u0600-\u06FF]{1,40}[)）]\s*$")
_QUOTES = "\"«»“”"


_EXPLAINED = re.compile(r"\s*[(（][^()（）]*(?:به معنای|معنی|اصطلاح|منظور)[^()（）]*[)）]")
_OR_LINE = re.compile(r"\n\s*یا\s*:?\s*\n")


def tidy_line(source: str, fa: str) -> str:
    """One subtitle line: no offered alternatives («… یا …», «… / …»), no explanation
    of the idiom, no Latin gloss at the end, and no quotation marks around the whole
    line when the original had none."""
    out = fa.strip()
    if "\n" in out and "\n" not in source:
        first = _OR_LINE.split(out)[0] if _OR_LINE.search(out) else out
        out = " ".join(x.strip() for x in first.splitlines() if x.strip())
    if " / " in out and "/" not in source:
        out = out.split(" / ")[0]
    out = _EXPLAINED.sub("", out).strip()
    out = _ALTERNATIVE.sub("", out).strip()
    if _LATIN_GLOSS.search(out) and not re.search(r"[(（]", source):
        out = _LATIN_GLOSS.sub("", out).strip()
    if len(out) > 2 and out[0] in _QUOTES and out[-1] in _QUOTES and not any(q in source for q in _QUOTES):
        out = out[1:-1].strip()
    return out or fa


def needs_translation(text: str) -> bool:
    letters = _LETTER.findall(_URLISH.sub(" ", text))
    return len(letters) >= 2 and not persian.looks_persian(text)


def plan_pieces(text: str) -> list[tuple[str, bool, str]]:
    """-> [(piece, translate?, separator_before)] preserving the layout."""
    pieces: list[tuple[str, bool, str]] = []
    buf: list[str] = []
    pending_sep = ""

    def flush():
        nonlocal pending_sep
        if buf:
            pieces.append(("\n\n".join(buf), True, pending_sep))
            buf.clear()
            pending_sep = "\n\n"

    for para in re.split(r"\n[ \t]*\n", text.strip()):
        para = para.strip("\n")
        if not para.strip():
            continue
        if not needs_translation(para):
            flush()
            pieces.append((para, False, pending_sep))
            pending_sep = "\n\n"
        elif len(para) > MAX_PIECE_CHARS:
            flush()
            sep = pending_sep
            for part in _split_long(para):
                pieces.append((part, True, sep))
                sep = " "
            pending_sep = "\n\n"
        else:
            if buf and len("\n\n".join(buf + [para])) > MAX_PIECE_CHARS:
                flush()
            buf.append(para)
    flush()
    return pieces


def _split_long(para: str) -> list[str]:
    out, cur = [], ""
    for sent in _SENTENCE_END.split(para):
        if cur and len(cur) + len(sent) + 1 > MAX_PIECE_CHARS:
            out.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}" if cur else sent
    if cur:
        out.append(cur)
    # a single enormous "sentence" (no punctuation) is cut on whitespace
    final = []
    for piece in out:
        while len(piece) > MAX_PIECE_CHARS * 1.5:
            cut = piece.rfind(" ", 0, MAX_PIECE_CHARS)
            cut = cut if cut > 0 else MAX_PIECE_CHARS
            final.append(piece[:cut])
            piece = piece[cut:].lstrip()
        final.append(piece)
    return final


class Cache:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY, v TEXT)")
        self.db.commit()

    @staticmethod
    def key(model: str, text: str) -> str:
        return hashlib.sha256(f"raw1\x00{model}\x00{text}".encode()).hexdigest()

    def get(self, model, text):
        with self.lock:
            row = self.db.execute("SELECT v FROM t WHERE k=?", (self.key(model, text),)).fetchone()
        return row[0] if row else None

    def put(self, model, text, value):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO t VALUES (?,?)", (self.key(model, text), value))
            self.db.commit()


def _ollama_error(status: int, body: str, model: str) -> TranslationError:
    try:
        msg = json.loads(body).get("error", body)
    except ValueError:
        msg = body
    if status == 404 or "not found" in msg:
        return TranslationError(f"مدل «{model}» هنوز دانلود نشده است. در ترمینال بزنید: ollama pull {model}")
    return TranslationError(f"خطای Ollama: {msg[:300]}")


_OFFLINE = "Ollama روشن نیست. در ترمینال بزنید: brew services start ollama"


class Translator:
    def __init__(self, cfg, cache_path):
        import httpx

        self.cfg = cfg
        self.url = cfg["ollama_url"].rstrip("/") + "/api/chat"
        self.cache = Cache(cache_path)
        timeout = httpx.Timeout(connect=5, read=600, write=60, pool=10)
        self.aclient = httpx.AsyncClient(timeout=timeout)
        self.client = httpx.Client(timeout=timeout)

    def options(self, model):
        if model == self.cfg["email_model"]:  # long e-mails
            return OPTIONS
        return {**OPTIONS, "num_ctx": SMALL_CTX}

    def _payload(self, model, text, src, stream):
        return {"model": model, "stream": stream, "keep_alive": self.cfg["keep_alive"],
                "options": self.options(model),
                "messages": [{"role": "user", "content": prompt_for(text, src)}]}

    # ---------- async, streaming (e-mail panel) ----------
    async def _stream_piece(self, model, text, src):
        import httpx

        try:
            async with self.aclient.stream("POST", self.url,
                                           json=self._payload(model, text, src, True)) as r:
                if r.status_code != 200:
                    raise _ollama_error(r.status_code, (await r.aread()).decode(errors="replace"), model)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        raise TranslationError(f"خطای Ollama: {obj['error']}")
                    delta = obj.get("message", {}).get("content", "")
                    if delta:
                        yield delta
        except httpx.ConnectError as e:
            raise TranslationError(_OFFLINE) from e

    async def stream(self, text: str, model: str, src: str | None = None):
        """Yields events: meta, delta (raw text as it arrives), piece, done."""
        pieces = plan_pieces(text)
        todo = "\n\n".join(p for p, do, _ in pieces if do)
        src = src or (langs.detect(todo) if todo else "fa")
        yield {"type": "meta", "source": src, "source_fa": langs.name_fa(src), "model": model,
               "pieces": len(pieces)}
        done_parts = []
        for piece, do, sep in pieces:
            if sep:
                yield {"type": "delta", "text": sep}
            if not do:
                out = piece
                yield {"type": "delta", "text": out}
            else:
                psrc = langs.detect(piece) if len(piece) > 200 else src
                if psrc == "fa":
                    psrc = src
                asked = idioms.plain(piece) if psrc == "en" else piece  # idioms -> plain meaning
                raw = self.cache.get(model, asked)
                if raw is None:
                    parts = []
                    async for d in self._stream_piece(model, asked, psrc):
                        parts.append(d)
                        yield {"type": "delta", "text": d}
                    raw = "".join(parts)
                    self.cache.put(model, asked, raw)
                else:
                    yield {"type": "delta", "text": raw}
                out = glossary.apply(piece, persian.normalize(raw))
            done_parts.append(sep + out)
            yield {"type": "piece", "done": len(done_parts), "of": len(pieces)}
        yield {"type": "done", "text": "".join(done_parts).strip(), "source": src}

    # ---------- sync (dubbing threads, simple API) ----------
    def translate(self, text: str, model: str, src: str | None = None) -> str:
        import httpx

        text = text.strip()
        if not text:
            return ""
        if "\n" not in text and (src or "en") == "en":
            direct = idioms.whole(text)  # «Copy that.», «Big deal!»: the Persian idiom itself
            if direct:
                return persian.normalize(direct)
        asked = idioms.plain(text) if (src or "en") == "en" else text  # idioms -> plain meaning
        cached = self.cache.get(model, asked)
        if cached is not None:
            return glossary.apply(text, persian.normalize(cached))
        src = src or langs.detect(text)
        try:
            r = self.client.post(self.url, json=self._payload(model, asked, src, False))
        except httpx.ConnectError as e:
            raise TranslationError(_OFFLINE) from e
        if r.status_code != 200:
            raise _ollama_error(r.status_code, r.text, model)
        raw = r.json().get("message", {}).get("content", "").strip()
        self.cache.put(model, asked, raw)
        return glossary.apply(text, persian.normalize(raw))

    def translate_lines(self, lines: list[str], model: str, src: str) -> list[str]:
        direct = [idioms.whole(ln) if src == "en" else None for ln in lines]
        rest = [ln for ln, d in zip(lines, direct) if not d]
        done = iter(self._translate_lines(rest, model, src))
        out = [persian.normalize(d) if d else next(done) for d in direct]
        return [tidy_line(a, b) for a, b in zip(lines, out)]

    def _translate_lines(self, lines: list[str], model: str, src: str) -> list[str]:
        """One line per subtitle sentence; falls back to line-by-line
        translation if the model merges or splits lines."""
        if not lines:
            return []
        joined = self.translate("\n".join(lines), model, src)
        out = [ln.strip() for ln in joined.split("\n") if ln.strip()]
        if len(out) == len(lines):
            return out
        return [self.translate(ln, model, src) for ln in lines]

    def unload(self, model: str):
        """Free a loaded model's memory now (no-op when it is not loaded)."""
        base = self.cfg["ollama_url"].rstrip("/")
        try:
            loaded = {m["name"] for m in self.client.get(base + "/api/ps", timeout=3).json().get("models", [])}
            if model in loaded or f"{model}:latest" in loaded:
                self.client.post(base + "/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
        except Exception:
            pass

    def model_available(self, model: str) -> bool | None:
        try:
            r = self.client.get(self.cfg["ollama_url"].rstrip("/") + "/api/tags", timeout=3)
            names = {m["name"] for m in r.json().get("models", [])}
            return model in names or f"{model}:latest" in names
        except Exception:
            return None
