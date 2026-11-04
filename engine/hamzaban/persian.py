"""Persian text helpers: script detection and a conservative clean-up pass.

The clean-up only touches things that are unambiguous in machine-translated
Persian: Arabic letter forms, spacing around punctuation, and the zero-width
non-joiner (نیم‌فاصله) in the few places where it is always wanted
(می‌/نمی‌ prefixes, the plural «ها» and the comparative «تر/ترین»).
URLs, e-mail addresses and code spans are never modified.
"""
import re

ZWNJ = "‌"

# Arabic-script letters (Persian letters included), no digits or punctuation.
_L = "ء-يٱ-ۓ"
_LETTER = re.compile(r"[^\W\d_]")
_AR_SCRIPT = re.compile(f"[{_L}]")

_PROTECT = re.compile(
    r"(https?://\S+|www\.\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|`[^`\n]*`)"
)

_RULES = [
    # Arabic letter forms -> Persian
    (re.compile("[يى]"), "ی"),  # ي ى -> ی
    (re.compile("ك"), "ک"),  # ك -> ک
    # ASCII punctuation next to Persian letters -> Persian punctuation
    (re.compile(f"(?<=[{_L}])[ ]*,"), "،"),
    (re.compile(f"(?<=[{_L}])[ ]*;"), "؛"),
    (re.compile(f"(?<=[{_L}])[ ]*\\?"), "؟"),
    # no space before punctuation, one space after it
    (re.compile(r"[ \t]+([،؛؟!.:»)])"), r"\1"),
    (re.compile(f"([،؛؟!])(?=[{_L}«(])"), r"\1 "),
    (re.compile(r"«[ \t]+"), "«"),
    # ZWNJ after the verbal prefixes می / نمی (not before function words)
    (re.compile(f"(?<![{_L}{ZWNJ}])(ن?می)[ ]+(?!(?:و|به|را|که|از|با|در|تا|هم)(?![{_L}]))(?=[{_L}])"),
     "\\1" + ZWNJ),
    # ZWNJ before the plural and comparative suffixes
    (re.compile(f"(?<=[{_L}])[ ]+(ها|های|هایی|هایم|هایت|هایش|هایمان|هایتان|هایشان|تر|ترین)(?![{_L}])"),
     ZWNJ + "\\1"),
    # tidy spaces and stray ZWNJs
    (re.compile(f"{ZWNJ}{{2,}}"), ZWNJ),
    (re.compile(f"{ZWNJ}(?=[ \\n])|(?<=[ \\n]){ZWNJ}"), ""),
    (re.compile(r"[ \t]{2,}"), " "),
    (re.compile(r"[ \t]+\n"), "\n"),
]


_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
# a free-standing number (not part of "Q3", "3pm", "v2.1-beta", a path, ...)
_NUMBER = re.compile(r"(?<![A-Za-z0-9_\-/#@.])\d+(?:[.,:/]\d+)*(?![A-Za-z0-9_\-/])")

# Small models sometimes finish a Persian word in another script
# ("کاتрин"); spell such stray letters in Persian.
_STRAY = {
    **dict(zip("абвгдезийклмнопрстуфхыэ", "ابوگدززییکلمنوپرستوفخیا")),
    "ж": "ژ", "ш": "ش", "ч": "چ", "ц": "تس", "щ": "شچ", "ю": "یو", "я": "یا", "ё": "یو", "ь": "", "ъ": "",
    **dict(zip("abdfghijklmnopqrstuvwyz", "ابدفگهیجکلمنوپکرستووییز")), "c": "ک", "e": "", "x": "کس",
}
_MIXED_WORD = re.compile(f"[^\\s]*[{_L}][^\\s]*")


# Compounds that machine translation often leaves split. Hand-picked: the
# persian-writing dictionary also lists phrases like «که‌در» or «آن‌را», so it
# is only consulted for the unambiguous heads below, never for any word pair.
_FIXED = {
    "سه شنبه": "سه‌شنبه", "پنج شنبه": "پنج‌شنبه", "یک شنبه": "یک‌شنبه", "چهار شنبه": "چهارشنبه",
    "دو شنبه": "دوشنبه", "نرم افزار": "نرم‌افزار", "سخت افزار": "سخت‌افزار", "هم زمان": "هم‌زمان",
    "هم اکنون": "هم‌اکنون", "هیچ گونه": "هیچ‌گونه", "همان طور": "همان‌طور", "همین طور": "همین‌طور",
    "پیش نیاز": "پیش‌نیاز", "پیش زمینه": "پیش‌زمینه", "پس زمینه": "پس‌زمینه", "دست نویس": "دست‌نویس",
    "بین المللی": "بین‌المللی", "چشم انداز": "چشم‌انداز", "ان شاء الله": "ان‌شاءالله",
    "فارغ التحصیل": "فارغ‌التحصیل", "فرزند خواند": "فرزندخواند", "همه چیز": "همه‌چیز",
    "هیچ کس": "هیچ‌کس", "هیچ چیز": "هیچ‌چیز", "وب سایت": "وب‌سایت", "ایمیل ها": "ایمیل‌ها",
}
# matched at a word start; endings may follow (فارغ‌التحصیلان، نرم‌افزاری)
_FIXED_RX = re.compile(f"(?<![{_L}{ZWNJ}])({'|'.join(map(re.escape, _FIXED))})")
_NUM_WORDS = "یک|دو|سه|چهار|پنج|شش|هفت|هشت|نه|ده|دوازده|چند"
_NUM_COMPOUND = re.compile(
    f"(?<![{_L}{ZWNJ}])({_NUM_WORDS}) (ماهه|ساله|روزه|ساعته|هفته‌ای|جانبه|گانه|بعدی|رقمی)(?![{_L}])")
# adverbs written with tanvin: لطفا -> لطفاً
_TANVIN = re.compile(
    f"(?<![{_L}])(لطفا|واقعا|اصلا|کاملا|دقیقا|تقریبا|معمولا|قبلا|بعدا|حتما|فعلا|اخیرا|صرفا|ضمنا|"
    f"نسبتا|مثلا|احتمالا|مستقیما|شخصا|عمدتا|اساسا|ظاهرا|تدریجا|فورا|نهایتا|مجددا|اولا|ثانیا)(?![{_L}ً])")
# after a silent «ه»: ezafe «ی», possessives and verb endings attach with a ZWNJ
_AFTER_HEH = re.compile(f"(?<=[{_L}]ه) (ی|ای|ام|ات|اش|مان|تان|شان|اید|ایم|اند)(?![{_L}])")
# second halves that (almost) never stand alone; joined only if the dictionary knows the word
_HEADS = set("انگیز انگیزی آمیز پذیر پذیری ناپذیر آور آوری بخش گرا گرایی شناس شناسی مند مندی "
             "کننده کنندگان دهنده دهندگان گیری سازی گذاری پردازی نویس نویسی".split())
# the second word is only looked at, not consumed, so it can start the next pair
_PAIR = re.compile(f"(?<![{_L}{ZWNJ}])([{_L}]+) (?=([{_L}]+)(?![{_L}{ZWNJ}]))")
_dictionary: set | None = None


def _compounds() -> set:
    """ZWNJ compounds from the persian-writing word list (github.com/ali2000hos/persian-writing)."""
    global _dictionary
    if _dictionary is None:
        from .config import PERSIAN_WORDS

        try:
            with open(PERSIAN_WORDS, encoding="utf-8") as f:
                _dictionary = {w.strip() for w in f if ZWNJ in w}
        except OSError:
            _dictionary = set()
    return _dictionary


def _join_pair(m: re.Match) -> str:
    a, b = m.group(1), m.group(2)
    if (b in _HEADS or a == "بی") and f"{a}{ZWNJ}{b}" in _compounds():
        return a + ZWNJ
    return m.group(0)


def _fix_number(m: re.Match) -> str:
    s = m.group(0)
    s = re.sub(r"(?<=\d)\.(?=\d)", "٫", s)
    return s.translate(_FA_DIGITS)


def _fix_mixed_word(m: re.Match) -> str:
    w = m.group(0)
    # only a word that STARTS in Persian ("API‌ها" is a legit brand + suffix)
    if not re.search(r"[A-Za-zЀ-ӿ]", w) or not _AR_SCRIPT.match(w.lstrip("«(\"'")):
        return w
    return "".join(_STRAY.get(c.lower(), c) if re.match(r"[A-Za-zЀ-ӿ]", c) else c for c in w)


def arabic_script_share(text: str) -> float:
    letters = _LETTER.findall(text[:6000])
    if not letters:
        return 0.0
    return sum(1 for c in letters if _AR_SCRIPT.match(c)) / len(letters)


def looks_persian(text: str) -> bool:
    """True when the text is (mostly) Persian — not just Arabic script."""
    sample = text[:6000]
    if len(_LETTER.findall(sample)) < 8 or arabic_script_share(sample) < 0.5:
        return False
    fa = 3 * sum(sample.count(c) for c in "پچژگ") + sum(sample.count(c) for c in "یک")
    ar = 3 * sample.count("ة") + sum(sample.count(c) for c in "يكى") + 2 * sample.count(" ال")
    return fa >= ar


def _clean_span(s: str) -> str:
    for rx, rep in _RULES:
        s = rx.sub(rep, s)
    s = _FIXED_RX.sub(lambda m: _FIXED[m.group(1)], s)
    s = _NUM_COMPOUND.sub(f"\\1{ZWNJ}\\2", s)
    s = _TANVIN.sub("\\1\u064B", s)
    s = _AFTER_HEH.sub(f"{ZWNJ}\\1", s)
    s = _PAIR.sub(_join_pair, s)
    s = _MIXED_WORD.sub(_fix_mixed_word, s)
    s = _NUMBER.sub(_fix_number, s)
    return re.sub(r"(?<=[۰-۹])\s?%", "٪", s)


def normalize(text: str) -> str:
    """Clean machine-translated Persian without touching URLs/e-mails/code."""
    parts = _PROTECT.split(text)
    # split() with one group: even indexes are plain text, odd are protected
    out = [p if i % 2 else _clean_span(p) for i, p in enumerate(parts)]
    return "".join(out).strip()
