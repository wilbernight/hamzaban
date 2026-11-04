"""Say numbers the way a Persian speaker reads them (for the TTS voice only).

The voices misread digits («۴۵۰۰» came out as a nonsense word, «۱۰۰» as «سود»)
but read number words perfectly, so before synthesis every free number and its
symbols become words: ۴٬۵۰۰ -> «چهار هزار و پانصد», ۸٪ -> «هشت درصد»,
۱۰:۳۰ -> «ده و سی دقیقه», ۲-۳ -> «دو تا سه», ۴م -> «چهارم». Subtitles keep the
digits. Numbers glued to Latin letters (Q3, v2.1, mp3, 15GB), URLs and e-mail
addresses are left alone: speech.py owns Latin tokens (and can reuse
latin_number). Run before speech.speakable so «PHP ۸٫۱» is still recognised
as a version («هشت نقطه یک», not «هشت ممیز یک»).
"""
import re

Z = "‌"
_L = "ء-يٱ-ۓ"  # Arabic-script letters, no digits (as in persian.py)
_D = "0-9۰-۹٠-٩"
_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬–—‐‑×", "01234567890123456789.,----x")

_ONES = ("صفر یک دو سه چهار پنج شش هفت هشت نه ده یازده دوازده سیزده چهارده پانزده شانزده "
         "هفده هجده نوزده").split()
_TENS = ("", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود")
_HUNDREDS = ("", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد")
_SCALES = ("", "هزار", "میلیون", "میلیارد", "تریلیون")
_AND = " و "


def _below_thousand(n: int) -> str:
    h, r = divmod(n, 100)
    parts = [_HUNDREDS[h]] if h else []
    if r >= 20:
        parts += [_TENS[r // 10]] + ([_ONES[r % 10]] if r % 10 else [])
    elif r:
        parts.append(_ONES[r])
    return _AND.join(parts)


def digits_words(s: str) -> str:
    """Digit by digit, any digit set: «۰۹۱۲» -> «صفر نه یک دو» (phones, codes)."""
    return " ".join(_ONES[int(c)] for c in s.translate(_ASCII) if c in "0123456789")


def number_words(n: int) -> str:
    """Persian words for an integer: 4500 -> «چهار هزار و پانصد»."""
    if n < 0:
        return "منفی " + number_words(-n)
    if n == 0:
        return _ONES[0]
    if n >= 1000 ** len(_SCALES):
        return digits_words(str(n))
    groups, scale = [], 0
    while n:
        n, g = divmod(n, 1000)
        if g:
            w = _below_thousand(g)
            if scale:  # «هزار», not «یک هزار»; but «یک میلیون»
                w = _SCALES[1] if g == 1 and scale == 1 else f"{w} {_SCALES[scale]}"
            groups.append(w)
        scale += 1
    return _AND.join(reversed(groups))


_ORDINAL = {"یک": "یکم", "دو": "دوم", "سه": "سوم"}


def ordinal_words(n: int) -> str:
    """«چهارم», «بیست و سوم», «سی‌ام» (append «ین» for «چهارمین»)."""
    head, _, last = number_words(n).rpartition(" ")
    last = _ORDINAL.get(last) or (last + Z + "ام" if last.endswith("ی") else last + "م")
    return f"{head} {last}" if head else last


def latin_number(s: str) -> str:
    """A bare digit string (also one cut out of a Latin token): words, or digit
    by digit when it is long or starts with a zero (a code, not an amount)."""
    a = s.translate(_ASCII)
    if not (a.isascii() and a.isdigit()):
        return s
    if len(a) > 12 or (len(a) > 1 and a[0] == "0"):
        return digits_words(a)
    return number_words(int(a))


def _code(p: str) -> str:
    return digits_words(p) if len(p) > 3 else latin_number(p)


def _ip(parts: list) -> bool:
    return len(parts) == 4 and all(p == "0" or (p[0] != "0" and int(p) < 256) for p in parts)


def _amount(s: str) -> str | None:
    """An integer, a decimal or a number with thousands separators."""
    grouped = bool(re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s))
    if grouped:
        s = s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", s) and not _ip(s.split(".")):  # 1.000.000
        s, grouped = s.replace(".", ""), True
    i, dot, f = s.partition(".")
    if not i.isdigit() or dot and not f.isdigit():
        return None
    whole = number_words(int(i)) if grouped or dot else latin_number(i)
    f = f.rstrip("0")
    if not f:
        return whole
    if f == "5":
        return f"{whole} و نیم" if int(i) else "نیم"
    zeros = len(f) - len(f.lstrip("0"))  # ۰٫۰۵: the zeros are part of the value
    frac = digits_words(f) if len(f) > 3 else " ".join([_ONES[0]] * zeros + [number_words(int(f))])
    return f"{whole} ممیز {frac}"


def _clock(s: str) -> str | None:
    """۱۰:۳۰ -> «ده و سی دقیقه», ۱۰:۰۰ -> «ده»; ۴۵:۱۲ (over 24) is minutes:seconds."""
    m = re.fullmatch(r"(\d{1,2}):(\d\d)(?::(\d\d))?", s)
    if not m:
        return None
    h, mi, sec = (int(x or 0) for x in m.groups())
    if mi > 59 or sec > 59 or (h > 24 and m.group(3)):
        return None
    vals, units = ((h, mi, sec), ("", " دقیقه", " ثانیه")) if h <= 24 else ((h, mi), (" دقیقه", " ثانیه"))
    return _AND.join(number_words(v) + u for v, u in zip(vals, units) if v or not u)


def _read(s: str, version: bool = False) -> str:
    """Words for one run of digits and separators (ASCII digits, separators unified)."""
    if version and re.fullmatch(r"\d+\.\d+", s):
        return " نقطه ".join(map(latin_number, s.split(".")))
    w = _amount(s) or _clock(s)
    if w:
        return w
    a, dash, b = s.partition("-")
    if dash and "-" not in b:  # a range, only between two numbers
        return f"{_read(a)} تا {_read(b)}"
    parts, seps = re.split(r"[.,:/x-]", s), set(re.findall(r"[.,:/x-]", s))
    if seps == {"."}:  # 1.2.3, 192.168.1.1
        return " نقطه ".join(map(latin_number, parts))
    if seps == {"x"}:  # ۱۹۲۰×۱۰۸۰
        return " در ".join(map(latin_number, parts))
    if len(parts) == 2 and seps == {":"}:  # 16:9
        return f"{latin_number(parts[0])} به {latin_number(parts[1])}"
    if len(parts) == 2 and seps == {"/"} and parts[0][0] != "0" and 0 < int(parts[0]) < int(parts[1]) <= 10:
        return f"{number_words(int(parts[0]))} {ordinal_words(int(parts[1]))}"  # ۳/۴: «سه چهارم»
    if len(parts) == 3 and seps in ({"/"}, {"-"}) and 4 in (len(parts[0]), len(parts[2])) \
            and sum(len(p) <= 2 for p in parts) == 2:  # a date: ۱۴۰۳/۰۵/۱۲
        return "، ".join(number_words(int(p)) for p in parts)
    return "، ".join(map(_code, parts))  # phone numbers, codes, lists


_SYM = "$€£٪%"
_UNITS = {"%": "درصد", "٪": "درصد", "$": "دلار", "€": "یورو", "£": "پوند",
          "°": "درجه", "°C": f"درجه{Z}ی سانتی{Z}گراد", "°F": f"درجه{Z}ی فارنهایت"}
_SEP = r"[.,:/٫٬\-‐‑]|[  ]?[–—×][  ]?"
_RUN = rf"[{_D}]+(?:(?:{_SEP})[{_D}]+)*"
_LATIN = rf"[A-Za-z{_D}_.\-/:+#@]"
_NUMBERS = re.compile(
    # left as they are: URLs, e-mails and any token with a Latin letter in it (Q3, v2.1, 15GB)
    rf"(?P<skip>https?://\S+|www\.\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|{_LATIN}*[A-Za-z]{_LATIN}*)"
    rf"|(?P<sign>(?<![\w\-−])[-−])?(?:(?P<pre>[{_SYM}])[  ]?)?"
    rf"(?=(?P<run>{_RUN}))(?P=run)(?![A-Za-z_])"  # atomic: a number is never cut short
    rf"(?:{Z}?(?P<ord>امین|ام|مین|م)(?![{_L}{Z}])|{Z}?(?P<suf>[{_L}][{_L}{Z}]*)"
    rf"|[  ]?(?P<post>°[CF]?(?![A-Za-z])|[{_SYM}]))?"
)
_VERSION_BEFORE = re.compile(rf"(?:[A-Za-z][A-Za-z0-9+#]*|نسخه(?:{Z}?ی)?|ورژن)[  ]+$")
_HAS_DIGIT = re.compile(f"[{_D}]")


def _say(m: re.Match) -> str:
    if m.group("skip"):
        return m.group(0)
    text, start, end = m.string, m.start(), m.end()
    s = re.sub(r"\s", "", m.group("run").translate(_ASCII))
    ordinal, suffix = m.group("ord"), m.group("suf")
    unit = _UNITS.get(m.group("pre") or m.group("post") or "")
    if ordinal and s.isdigit() and len(s) <= 12:
        words = ordinal_words(int(s)) + ("ین" if ordinal.endswith("مین") else "")
    else:
        version = not unit and bool(_VERSION_BEFORE.search(text, max(0, start - 40), start))
        words, suffix = _read(s, version), suffix or ordinal
    if m.group("sign"):
        words = "منفی " + words
    if unit:
        words += " " + unit
    if suffix:  # ۵۰‌ها -> «پنجاه‌ها»
        words += Z + suffix
    if start and text[start - 1].isalpha():  # فصل۳ -> «فصل سه»
        words = " " + words
    if end < len(text) and text[end].isalnum():
        words += " "
    return words


_DIMENSIONS = re.compile(rf"(?<![\w.])([{_D}]+)[  ]?[xX×][  ]?([{_D}]+)(?![\w.])")

# ------------------------------------------------------------ dates
# A day before (or, English order, after) a month is an ordinal («پانزدهم مارس»), a
# numeric date is said with the month's name, «1990s» is a decade, «قرن ۲۱» an ordinal.
_GREGORIAN = ["ژانویه", "فوریه", "مارس", "آوریل", "مه", "ژوئن", "ژوئیه", "اوت", "سپتامبر", "اکتبر", "نوامبر", "دسامبر"]
_SOLAR = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
_ENGLISH_MONTHS = {m: _GREGORIAN[i] for i, ms in enumerate(
    [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
     ("july", "jul"), ("august", "aug"), ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"),
     ("december", "dec")]) for m in ms}
_MONTH_WORDS = sorted(set(_GREGORIAN + _SOLAR + ["می", "جولای", "آگوست", "مارچ"]), key=len, reverse=True)
_MONTH = "(?:" + "|".join(_MONTH_WORDS) + "|" + "|".join(sorted(_ENGLISH_MONTHS, key=len, reverse=True)) + ")"
_NUM = f"[{_D}]"
_DAY_MONTH = re.compile(rf"(?<![\w.])({_NUM}{{1,2}})(?:ام|م|th|st|nd|rd)?\s+({_MONTH})(?![\w])", re.I)
_MONTH_DAY = re.compile(rf"(?<![\w])({_MONTH})\.?\s+({_NUM}{{1,2}})(?:th|st|nd|rd)?(?![\w.:/])", re.I)
_YMD = re.compile(rf"(?<![\w.])({_NUM}{{4}})[/\-.]({_NUM}{{1,2}})[/\-.]({_NUM}{{1,2}})(?![\w.])")
_DECADE = re.compile(rf"(?<![\w.])(?:the\s+)?({_NUM}{{4}}|{_NUM}{{2}})['’]?(?:s|اس)(?![\w])", re.I)
_H = "[\u064B-\u0652]*"  # vowel marks a pronunciation fix may have added («قَرن»)
_CENTURY = re.compile(rf"(ق{_H}ر{_H}ن{_H}|سده)(?:‌ی|ی)?\s+({_NUM}{{1,2}})(?![{_D}\w])")
_DOUBLE_DECADE = re.compile(rf"دهه(?:‌ی|ی)?{_H}\s+(دهه‌ی )")


def _int(s: str) -> int:
    return int(s.translate(_ASCII))


def _month_name(word: str) -> str:
    return _ENGLISH_MONTHS.get(word.lower(), {"می": "مه", "جولای": "ژوئیه", "آگوست": "اوت", "مارچ": "مارس"}.get(word, word))


def _ymd(m: re.Match) -> str:
    y, mo, d = _int(m.group(1)), _int(m.group(2)), _int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    if 1250 <= y <= 1500:                       # Iranian calendar
        month = _SOLAR[mo - 1]
    elif 1700 <= y <= 2200:
        month = _GREGORIAN[mo - 1]
    else:
        return m.group(0)
    return f"{ordinal_words(d)} {month} {number_words(y)}"


def _dates(text: str) -> str:
    text = _YMD.sub(_ymd, text)
    text = _DAY_MONTH.sub(lambda m: f"{ordinal_words(_int(m.group(1)))} {_month_name(m.group(2))}"
                          if 1 <= _int(m.group(1)) <= 31 else m.group(0), text)
    text = _MONTH_DAY.sub(lambda m: f"{ordinal_words(_int(m.group(2)))} {_month_name(m.group(1))}"
                          if 1 <= _int(m.group(2)) <= 31 else m.group(0), text)
    text = _DECADE.sub(lambda m: f"دهه{Z}ی {number_words(_int(m.group(1)))}", text)
    text = _DOUBLE_DECADE.sub(r"\1", text)  # «دهه‌ی ۱۹۹۰s» was already «دهه‌ی …»
    return _CENTURY.sub(lambda m: f"{m.group(1)} {ordinal_words(_int(m.group(2)))}", text)


def speak_numbers(text: str) -> str:
    """Numbers and number symbols in (normalized) Persian text -> words, for the voice."""
    if not _HAS_DIGIT.search(text):
        return text
    text = _DIMENSIONS.sub(r"\1 در \2", text)  # 28x28, ۱۹۲۰×۱۰۸۰
    text = _dates(text)
    return _NUMBERS.sub(_say, text)
