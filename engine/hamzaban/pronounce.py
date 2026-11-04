"""How the voice should say particular words, applied to the text right before speech.

The voices read Persian without vowel marks and sometimes guess wrong or swallow
a short word: «کد» came out as «که» (dropped) but «کُد» is read right. Heavy
marking hurts («نامُرَتَّب» was read worse than «نامرتب»), so only words known to
go wrong get a light hint here. Subtitles keep the original spelling.

The user can add or override words in ~/Library/Application Support/Hamzaban/pronounce.txt:

    واژه = تلفظ          (e.g. کد = کُد, or an English word: Kubernetes = کوبرنتیز)
"""
import re
import threading
from pathlib import Path

from .config import APP_HOME

PRONOUNCE_FILE = APP_HOME / "pronounce.txt"
# every fix the user saves is also written into the code (see server.lexicon_add), so a
# lost or reset pronounce.txt loses nothing
WORDS_FILE = Path(__file__).parent / "words" / "pronounce.txt"
Z = "‌"

# Only hints that were both heard better and are linguistically right. A test on 1 Oct
# 2026 (150 dub sentences -> voice -> Whisper, then marked variants of the words heard
# wrong) showed Whisper cannot judge short vowels: it writes the right word for a wrong
# vowel, so most "winners" were wrong («رَکورد», «دِشواری») and were left out.
BUILTIN = {
    "کد": "کُد", "کدها": "کُدها", "کدهای": "کُدهای", "کدی": "کُدی", "کدنویسی": "کُدنویسی",
    "کد‌ها": "کُدها", "کد‌های": "کُدهای",
    "سطح": "سَطح", "سطحی": "سَطحی", "سطوح": "سُطوح",   # plain: heard 0 of 3, marked: 3 of 3
    "اپل": "اَپل",
    # compounds: the mark alone is often ignored; split at the joint it is read (4 of 4 vs 2 of 4)
    "نیمکره": "نیم‌کُره", "نیم‌کره": "نیم‌کُره",
    # «اشکال» is read eshkâl or ashkâl at random and a mark is ignored: the voice says the
    # same thing with a word it cannot misread (eshkâl = problem, far the commoner; the
    # "shapes" reading is set from the English before this, see HOMOGRAPHS)
    "اشکال": "مشکل", "اشکالات": "مشکلات", "اشکال‌زدایی": "دیباگ", "اشکالزدایی": "دیباگ",
}
# word swaps made for OmniVoice that would hurt ParSiGo, whose G2P reads «اشکال» right from
# the sentence itself («این اشکال هندسی» -> ?aSkAle, «اشکالی ندارد» -> ?eSkAli)
OMNI_ONLY = {"اشکال", "اشکالات", "اشکال‌زدایی", "اشکالزدایی"}
# default fixes in ParSiGo phonemes (its G2P ignores marks: «نیمکره», «نیمکُره» -> nimkare)
BUILTIN_PHONEMES = {"نیمکره": "nimkore", "دبلیو": "dabelyu"}
_MARKS = re.compile("[\u064B-\u0652\u200c ]")


def _respelling(word: str, say: str) -> bool:
    """Only marks or joints changed («قرن = قَرن», «نیمکره = نیم‌کُره»): useless to ParSiGo,
    whose G2P drops marks, and a joint even splits the word."""
    return _MARKS.sub("", word) == _MARKS.sub("", say)

# endings a word may carry: any after a ZWNJ; written straight on, only the unambiguous
# ones («مردم» is "people", not «مرد» + «م»)
_SUFFIX = ("(?:" + Z + "(?:هایی|هایم|هایت|هایش|هایمان|هایتان|هایشان|های|ها|ای|ام|ات|اش|مان|تان|شان|ی)"
           "|(?:هایی|های|ها|ی))?")


def stem(word: str) -> str:
    """The word without a ZWNJ-attached ending («پتانسیل‌هایی» -> «پتانسیل»)."""
    return re.sub(Z + "(?:هایی|هایم|هایت|هایش|هایمان|هایتان|هایشان|های|ها|ای|ام|ات|اش|ی)$", "", word.strip())


_lock = threading.Lock()
_cache = {"mtime": None, "rules": BUILTIN, "rx": None, "phonemes": {}}
# a fix written in ParSiGo's phoneme alphabet (Latin: A = آ, S = ش, C = چ, x = خ, ? = ء …)
_PHONEMES = re.compile(r"[A-Za-z?]+(?: [A-Za-z?]+)*")


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _load():
    mtime = (_mtime(WORDS_FILE), _mtime(PRONOUNCE_FILE))
    with _lock:
        if _cache["rx"] is not None and _cache["mtime"] == mtime:
            return _cache["rules"], _cache["rx"]
        rules = dict(BUILTIN)
        phonemes = dict(BUILTIN_PHONEMES)
        for path in (WORDS_FILE, PRONOUNCE_FILE):  # the code's copy, then the user's file
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    word, say = (x.strip() for x in line.split("=", 1))
                    if word and say and _PHONEMES.fullmatch(say):
                        phonemes[stem(word)] = say  # for ParSiGo only (see fix_phonemes)
                    elif word and say:
                        rules[word] = say
            except OSError:
                pass
        words = sorted(rules, key=len, reverse=True)
        # whole words, with or without Persian suffixes («پتانسیل‌های», «کدی»): the suffix is kept
        rx = re.compile(r"(?<![\w" + Z + r"])(" + "|".join(map(re.escape, words)) + r")(" + _SUFFIX
                        + r")(?![\w" + Z + r"])", re.IGNORECASE) if words else None
        # the same rules for a ParSiGo voice: no OmniVoice-only swaps, no respellings
        phonetic = {k: v for k, v in rules.items() if k not in OMNI_ONLY and not _respelling(k, v)}
        pwords = sorted(phonetic, key=len, reverse=True)
        prx = re.compile(r"(?<![\w" + Z + r"])(" + "|".join(map(re.escape, pwords)) + r")(" + _SUFFIX
                         + r")(?![\w" + Z + r"])", re.IGNORECASE) if pwords else None
        _cache.update(mtime=mtime, rules={k.lower(): v for k, v in rules.items()}, rx=rx, phonemes=phonemes,
                      prx=prx)
        return _cache["rules"], rx


def apply(text: str, phonetic: bool = False) -> str:
    """phonetic: for a ParSiGo voice (its G2P reads context; marks mean nothing to it)."""
    rules, rx = _load()
    if phonetic:
        rx = _cache.get("prx")
    if rx is None:
        return text
    return rx.sub(lambda m: rules.get(m.group(1).lower(), m.group(1)) + m.group(2), text)


# ------------------------------------------------------------ words with two readings
# One spelling, two words («اشکال»: eshkâl "problem" / ashkâl "shapes»). A rule for one
# reading would break the other, and the voice often ignores a mark on the first
# letter («اَشکال» was still read eshkâl). A dub knows the English it translated, so
# the reading is chosen from it; where a plain word says the same thing it is used
# instead of a mark (the subtitle keeps the original either way).
# word -> [(English cue, what the voice is given)]; no cue = the voice's usual reading.
# A reading ending in "|" + ezafe form is used before a describing word («شکل‌های هندسی»);
# one starting with "$" (a verb) is used only at the end of a clause («مرد مُرد»).
HOMOGRAPHS = {
    "اشکال": [(r"\b(shapes?|forms|figures|patterns|geometric)\b", "شکل‌ها|شکل‌های")],
    "کشتی": [(r"\bwrestl", "کُشتی"), (r"\b(ship|boat|vessel|sail|navy|cruise)", "کَشتی")],
    "مرد": [(r"\b(died|dies|dead|death|passed away)\b", "$مُرد")],
    "کردها": [(r"\b(kurd|kurds|kurdish)", "کُردها")],
    "شکر": [(r"\b(thank|grateful|gratitude|praise)", "شُکر"), (r"\bsugar", "شِکَر")],
    "پر": [(r"\b(feathers?|wings?|plumage)\b", "پَر"), (r"\b(full|filled|fill|packed|crowded)\b", "پُر")],
    "مهر": [(r"\b(seal|stamp|stamped)\b", "مُهر"), (r"\b(kindness|love|affection|mehr)\b", "مِهر")],
    "گرد": [(r"\b(round|circular|around)\b", "گِرد"), (r"\b(dust|powder)\b", "گَرد")],
    "خرد": [(r"\b(wisdom|wise|intellect|reason)\b", "خِرَد"), (r"\b(crush|smash|shatter|broke|broken|small change)", "خُرد")],
    "کشت": [(r"\b(kill|killed|kills|murder)", "$کُشت"), (r"\b(cultivat|crop|farm|grow|plant)", "کِشت")],
    "کره": [(r"\b(korea|sphere|globe|planet|earth|ball|hemisphere)", "کُره"), (r"\bbutter", "کَره")],
    "ملک": [(r"\b(king|monarch)\b", "مَلِک"), (r"\bangel", "فرشته"), (r"\b(property|estate|land)\b", "مِلک")],
    "حکم": [(r"\b(referee|umpire|arbiter)", "حَکَم")],
    "سم": [(r"\b(hoof|hooves)", "سُم"), (r"\b(poison|toxic|venom)", "سَم")],
}


def in_context(fa: str, source: str) -> str:
    """Persian about to be spoken -> the right reading of two-reading words, chosen by
    the English sentence it was translated from."""
    if not source:
        return fa
    low = source.lower()
    for word, readings in HOMOGRAPHS.items():
        if word not in fa:
            continue
        for cue, say in readings:
            if re.search(cue, low):
                fa = re.sub(r"(?<![\w" + Z + r"])" + re.escape(word) + r"(?![\w" + Z + r"])(?=(\s*)(\S*))",
                            lambda m: _reading(say, m.group(2)) or m.group(0), fa)
                break
    return fa


# words after which a noun takes no ezafe (verbs, particles), and clause ends
_NO_EZAFE = {"را", "و", "که", "از", "در", "به", "با", "تا", "هم", "نیز", "است", "هستند", "بود", "بودند", "شد",
             "شدند", "دارد", "دارند", "می", "را،", "یا", "اما", "ولی", "این", "آن"}


def _reading(say: str, next_word: str) -> str | None:
    word_after = next_word.strip(".,!?؟،;:«»\"'")
    at_end = not word_after or next_word[:1] in ".,!?؟،;:" or word_after in ("و", "که")
    if say.startswith("$"):  # a verb reading: only where verbs stand
        return say[1:] if at_end else None
    if "|" in say:
        plain, ezafe = say.split("|", 1)
        takes_ezafe = word_after and not at_end and word_after not in _NO_EZAFE and not word_after.startswith("می")
        return ezafe if takes_ezafe else plain
    return say


# ------------------------------------------------------------ fixes in phonemes (ParSiGo)
# ParSiGo turns text into phonemes first (its G2P understands context and ezafe, but
# ignores vowel marks like the other voices: «نیمکره» and «نیمکُره» are both nimkare)
# and says phonemes exactly. A fix written in phonemes («نیمکره = nimkore») is put in
# place of that word's phonemes, so it always takes effect with a ParSiGo voice.
def phoneme_rules() -> dict:
    _load()
    return _cache["phonemes"]


def signature() -> str:
    """Changes whenever a phoneme fix does (for caches keyed on the text)."""
    rules = phoneme_rules()
    return ";".join(f"{k}={v}" for k, v in sorted(rules.items()))


_PUNCT = "\u060c\u061b\u061f.,!?;:«»\"'()"


def fix_phonemes(text: str, ph: str, phonemise) -> str:
    """ParSiGo phonemes for text -> with the user's phoneme fixes put in.
    phonemise: ParSiGo's own G2P (to find what it says for the word alone)."""
    rules = phoneme_rules()
    if not rules or not ph:
        return ph
    words_t = [stem(w.strip(_PUNCT)) for w in text.split()]
    if not any(w in rules for w in words_t):
        return ph
    words_p = ph.split()

    def put(i, fixed):
        old = words_p[i]
        tail = "ye1" if old.endswith("ye1") else ("e1" if old.endswith("e1") else "")  # keep the ezafe
        if tail and not fixed.endswith(tail[:-1]):
            tail = ("ye1" if fixed[-1] in "aeiouA" else "e1")
        words_p[i] = fixed + tail

    if len(words_t) == len(words_p):  # one phoneme word per text word: replace in place
        for i, w in enumerate(words_t):
            if w in rules:
                put(i, rules[w])
        return " ".join(words_p)
    for w in set(words_t) & set(rules):  # otherwise: where the word's own phonemes appear
        alone = (phonemise(w) or "").split()
        if len(alone) != 1:
            continue
        for i, pw in enumerate(words_p):
            if pw == alone[0] or pw in (alone[0] + "e1", alone[0] + "ye1"):
                put(i, rules[w])
    return " ".join(words_p)


def split_repeats(text: str) -> list[tuple[str, int]]:
    """text -> [(piece, times)], a word said several times in a row as one piece.
    ParSiGo's G2P loses count on repeats: «دبلیو دبلیو دبلیو» comes out 8 or 2
    times, «نه نه نه نه» as 30 «na»s; so a repeated word is phonemised once."""
    words = text.split()
    out, plain, i = [], [], 0
    while i < len(words):
        key = words[i].strip(_PUNCT)
        j = i + 1
        while key and j < len(words) and words[j].strip(_PUNCT) == key:
            j += 1
        if j - i > 1:
            if plain:
                out.append((" ".join(plain), 1))
                plain = []
            out.append((key, j - i))
        else:
            plain.append(words[i])
        i = j
    if not any(n > 1 for _, n in out):
        return [(text, 1)]
    if plain:
        out.append((" ".join(plain), 1))
    return out


def phonemise_repeats(text: str, g2p) -> str:
    """g2p(text) with every repeated word said once, then its phonemes repeated.
    The word keeps its sentence around it (alone, «دبلیو» comes out «dabeliv»)."""
    pieces = split_repeats(text)
    if len(pieces) == 1 and pieces[0][1] == 1:
        return g2p(text)
    once = " ".join(p for p, _ in pieces)
    ph = g2p(once).split()
    if len(ph) != len(once.split()):  # no word-for-word match: each piece on its own
        return " ".join(" ".join([g2p(p)] * n) for p, n in pieces).strip()
    out, i = [], 0
    for piece, n in pieces:
        k = len(piece.split())
        seg = ph[i:i + k]
        i += k
        if n > 1:  # only the last of the repeats takes the ezafe
            seg = [re.sub(r"y?e1$", "", seg[0])] * (n - 1) + seg
        out += seg
    return " ".join(out)
