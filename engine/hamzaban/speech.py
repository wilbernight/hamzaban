"""Make Latin-script words inside Persian text speakable.

The Persian voice spells unknown English words letter by letter, so before
synthesis every Latin token is rewritten the way a Persian developer says it:
PaymentGateway.php -> «پیمنت گیت‌وی دات پی‌اچ‌پی», API -> «ای‌پی‌آی»,
composer dump-autoload -> «کامپوزر دامپ اتولود». Subtitles keep the original.
"""
import re

Z = "‌"

LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz", [
    "ای", "بی", "سی", "دی", "ای", "اف", "جی", "اچ", "آی", "جی", "کی", "ال", "ام", "ان", "او", "پی",
    "کیو", "آر", "اس", "تی", "یو", "وی", "دبلیو", "ایکس", "وای", "زد"]))

WORDS = {
    # languages, tools, platforms
    "php": "پی‌اچ‌پی", "laravel": "لاراول", "symfony": "سیمفونی", "composer": "کامپوزر", "artisan": "آرتیزان",
    "eloquent": "الوکوئنت", "blade": "بلید", "python": "پایتون", "django": "جنگو", "flask": "فلسک",
    "javascript": "جاوااسکریپت", "typescript": "تایپ‌اسکریپت", "java": "جاوا", "kotlin": "کاتلین",
    "swift": "سوئیفت", "react": "ری‌اکت", "vue": "ویو", "angular": "انگولار", "node": "نود", "nodejs": "نود جی‌اس",
    "npm": "ان‌پی‌ام", "yarn": "یارن", "git": "گیت", "github": "گیت‌هاب", "gitlab": "گیت‌لب", "docker": "داکر",
    "kubernetes": "کوبرنتیز", "linux": "لینوکس", "ubuntu": "اوبونتو", "windows": "ویندوز", "macos": "مک‌اواس",
    "android": "اندروید", "ios": "آی‌اواس", "mysql": "مای‌اس‌کیوال", "postgres": "پستگرس", "postgresql": "پستگرس",
    "sqlite": "اس‌کیولایت", "redis": "ردیس", "mongodb": "مانگو دی‌بی", "nginx": "انجین‌ایکس", "apache": "آپاچی",
    "vscode": "وی‌اس‌کد", "phpstorm": "پی‌اچ‌پی استورم", "xdebug": "ایکس‌دیباگ", "json": "جیسون", "yaml": "یامل",
    "html": "اچ‌تی‌ام‌ال", "css": "سی‌اس‌اس", "sql": "اس‌کیوال", "http": "اچ‌تی‌تی‌پی", "https": "اچ‌تی‌تی‌پی‌اس",
    "url": "یوآرال", "api": "ای‌پی‌آی", "cli": "سی‌ال‌آی", "ide": "آی‌دی‌ای", "pdf": "پی‌دی‌اف",
    # vocabulary
    "code": "کد", "class": "کلاس", "classes": "کلاس‌ها", "interface": "اینترفیس", "interfaces": "اینترفیس‌ها",
    "abstract": "ابسترکت", "trait": "تریت", "enum": "اینام", "function": "فانکشن", "method": "متد", "methods": "متدها",
    "object": "آبجکت", "array": "آرایه", "string": "استرینگ", "integer": "اینتیجر", "int": "اینت", "float": "فلوت",
    "bool": "بول", "boolean": "بولین", "null": "نال", "true": "ترو", "false": "فالس", "void": "وید",
    "return": "ریترن", "public": "پابلیک", "private": "پرایوت", "protected": "پروتکتد", "static": "استاتیک",
    "final": "فاینال", "readonly": "ریدآنلی", "extends": "اکستندز", "implements": "ایمپلیمنتس", "new": "نیو",
    "const": "کانست", "var": "ور", "echo": "اکو", "print": "پرینت", "foreach": "فوریچ", "match": "مچ",
    "namespace": "نیم‌اسپیس", "use": "یوز", "require": "ریکوایر", "include": "اینکلود", "autoload": "اتولود",
    "autoloader": "اتولودر", "dump": "دامپ", "vendor": "وندور", "package": "پکیج", "packages": "پکیج‌ها",
    "module": "ماژول", "plugin": "پلاگین", "library": "لایبرری", "framework": "فریم‌ورک", "commit": "کامیت",
    "merge": "مرج", "branch": "برنچ", "push": "پوش", "pull": "پول", "request": "ریکوئست", "requests": "ریکوئست‌ها",
    "response": "ریسپانس", "deploy": "دیپلوی", "container": "کانتینر", "server": "سرور", "client": "کلاینت",
    "database": "دیتابیس", "query": "کوئری", "cache": "کش", "debug": "دیباگ", "bug": "باگ", "error": "ارور",
    "exception": "اکسپشن", "log": "لاگ", "test": "تست", "tests": "تست‌ها", "unit": "یونیت", "route": "روت",
    "router": "روتر", "controller": "کنترلر", "model": "مدل", "view": "ویو", "middleware": "میدل‌ور",
    "migration": "مایگریشن", "seeder": "سیدر", "factory": "فکتوری", "repository": "ریپازیتوری",
    "service": "سرویس", "provider": "پروایدر", "instance": "اینستنس",
    "constructor": "کانستراکتور", "property": "پراپرتی", "callback": "کال‌بک", "closure": "کلوژر",
    "promise": "پرامیس", "async": "اسینک", "await": "اویت", "token": "توکن", "session": "سشن", "cookie": "کوکی",
    "hash": "هش", "password": "پسورد", "login": "لاگین", "user": "یوزر", "admin": "ادمین", "frontend": "فرانت‌اند",
    "backend": "بک‌اند", "web": "وب", "website": "وب‌سایت", "app": "اپ", "terminal": "ترمینال", "shell": "شل",
    "bash": "بش", "script": "اسکریپت", "file": "فایل", "folder": "فولدر", "config": "کانفیگ", "index": "ایندکس",
    "main": "مین", "src": "سورس", "readme": "ریدمی", "localhost": "لوکال‌هاست", "payment": "پیمنت",
    "gateway": "گیت‌وی", "charge": "چارج", "hello": "هلو", "world": "ورلد", "email": "ایمیل",
    # brands and sites
    "google": "گوگل", "youtube": "یوتیوب", "chrome": "کروم", "firefox": "فایرفاکس", "gmail": "جیمیل",
    "udemy": "یودمی", "stanford": "استنفورد", "apple": "اپل", "iphone": "آیفون", "microsoft": "مایکروسافت",
    "amazon": "آمازون", "aws": "ای‌دبلیواس", "openai": "اوپن‌ای‌آی", "chatgpt": "چت‌جی‌پی‌تی", "claude": "کلود",
    "linkedin": "لینکدین", "telegram": "تلگرام", "instagram": "اینستاگرام", "whatsapp": "واتس‌اپ",
    "zoom": "زوم", "slack": "اسلک", "jira": "جیرا", "figma": "فیگما", "excel": "اکسل",
    # months (the voice would say «مارچ»): the names Persian uses
    "january": "ژانویه", "february": "فوریه", "march": "مارس", "april": "آوریل", "june": "ژوئن",
    "july": "ژوئیه", "august": "اوت", "september": "سپتامبر", "october": "اکتبر", "november": "نوامبر",
    "december": "دسامبر",
    # web address parts
    "www": "دبلیو دبلیو دبلیو", "com": "کام", "org": "اورگ", "net": "نت", "edu": "ای‌دی‌یو", "io": "آی‌او",
    "ir": "آی‌آر", "dev": "دِو", "js": "جی‌اس", "ts": "تی‌اس", "py": "پای", "md": "ام‌دی", "txt": "تکست",
}
SYMBOLS = {".": "دات", "@": "ات", "/": "اسلش", "+": "پلاس", "#": "شارپ"}

_TOKEN = re.compile(r"(?:https?://)?[A-Za-z][A-Za-z0-9_.@/+#\-]*[A-Za-z0-9+#]|[A-Za-z]")
_PIECES = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def spell(word: str) -> str:
    return Z.join(LETTERS[c] for c in word.lower() if c in LETTERS)


# ---------------------------------------------------------------- any English word
# The CMU pronouncing dictionary (126K words, ARPAbet) says how a word sounds; that is
# written the way Persian writes English loanwords: long vowels as letters, short
# ones mostly left out (network -> نتورک, pixel -> پیکسل, function -> فانکشن).
_CONS = {"B": "ب", "CH": "چ", "D": "د", "DH": "د", "F": "ف", "G": "گ", "HH": "ه", "JH": "ج", "K": "ک",
         "L": "ل", "M": "م", "N": "ن", "NG": "نگ", "P": "پ", "R": "ر", "S": "س", "SH": "ش", "T": "ت",
         "TH": "ت", "V": "و", "W": "و", "Y": "ی", "Z": "ز", "ZH": "ژ"}
# vowel -> (word start, middle, word end)
_VOWELS = {"AA": ("آ", "ا", "ا"), "AO": ("آ", "ا", "ا"), "AE": ("ا", "", "ا"), "EH": ("ا", "", "ه"),
           "AH": ("ا", "", "ا"), "AH1": ("آ", "ا", "ا"), "AW": ("آو", "او", "او"), "AY": ("آی", "ای", "ای"),
           "ER": ("ار", "ر", "ر"), "EY": ("ای", "ی", "ی"), "IH": ("ای", "ی", "ی"), "IY": ("ای", "ی", "ی"),
           "OW": ("او", "و", "و"), "OY": ("اوی", "وی", "وی"), "UH": ("او", "و", "و"), "UW": ("او", "و", "و")}
_cmu = None


def _cmudict():
    global _cmu
    if _cmu is None:
        try:
            import cmudict

            _cmu = cmudict.dict()
        except Exception:  # not installed: the rules below still give a fair reading
            _cmu = {}
    return _cmu


def _from_phones(phones) -> str:
    out = []
    n = len(phones)
    for i, ph in enumerate(phones):
        base = ph.rstrip("012")
        if base in _CONS:
            # «st», «sp», «sk»… at the start take a leading vowel in Persian (استریم، اسکریپت)
            if i == 0 and base == "S" and n > 1 and phones[1].rstrip("012") in _CONS:
                out.append("ا")
            nxt = phones[i + 1].rstrip("012") if i + 1 < n else ""
            out.append("ن" if base == "NG" and nxt in ("K", "G") else _CONS[base])  # think -> تینک
            continue
        key = "AH1" if base == "AH" and ph.endswith(("1", "2")) else base
        start, mid, end = _VOWELS.get(key, ("ا", "", "ا"))
        prev_vowel = i > 0 and phones[i - 1].rstrip("012") not in _CONS
        if base == "ER" and i > 0 and phones[i - 1].rstrip("012") == "R":
            out.append("ور")  # error -> ارور, mirror -> میرور
        elif i == 0:
            out.append(start)
        elif i == n - 1:
            out.append(end)
        elif prev_vowel and not mid:
            out.append("ا")  # two vowels in a row: re-act -> ری‌اکت, not ریکت
        else:
            out.append(mid)
    return "".join(out)


_RULES = [("tion", "شن"), ("sion", "ژن"), ("ough", "اف"), ("igh", "ای"), ("ph", "ف"), ("sh", "ش"),
          ("ch", "چ"), ("th", "ت"), ("ck", "ک"), ("qu", "کو"), ("ee", "ی"), ("ea", "ی"), ("oo", "و"),
          ("ou", "او"), ("ai", "ی"), ("ay", "ی"), ("oa", "و"), ("ce", "س"), ("ci", "سی"), ("cy", "سی"),
          ("ge", "ج"), ("x", "کس")]
_LETTER = dict(zip("abcdefghijklmnopqrstuvwxyz", ["ا", "ب", "ک", "د", "", "ف", "گ", "ه", "ی", "ج", "ک", "ل",
                                                  "م", "ن", "و", "پ", "ک", "ر", "س", "ت", "ا", "و", "و", "کس",
                                                  "ی", "ز"]))


def _by_rules(w: str) -> str:
    """Words the dictionary does not know (names, new products): rough letter rules."""
    w = w.lower()
    out, i = [], 0
    if w[0] in "aeiou":
        out.append({"a": "آ", "u": "یو"}.get(w[0], "ا"))
        i = 1 if w[0] in "aeu" else 0
    elif w[0] == "s" and len(w) > 1 and w[1] in "tpkmnlw":
        out.append("ا")
    while i < len(w):
        for src, dst in _RULES:
            if w.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            c = w[i]
            out.append("ه" if c == "e" and i == len(w) - 1 and len(w) <= 3 else _LETTER.get(c, ""))
            i += 1
    return "".join(out)


def english(w: str) -> str:
    """One English word in Persian letters, the way it is pronounced."""
    phones = _cmudict().get(w.lower())
    return _from_phones(phones[0]) if phones else _by_rules(w)


def _word(w: str) -> str:
    low = w.lower()
    if low in WORDS:
        return WORDS[low]
    if w.isdigit():
        return w
    if w.isupper() and len(w) <= 6 and (len(w) <= 3 or low not in _cmudict()):
        return spell(w)  # API, URL, CPU (but NASA, LINUX are words)
    return english(w)


def _token(tok: str) -> str:
    tok = re.sub(r"^https?://", "", tok)
    if "/" in tok and "." in tok.split("/")[0]:
        tok = tok.split("/")[0]  # a web address: the site name is enough
    whole = WORDS.get(tok.lower())
    if whole:
        return whole
    out = []
    for part in re.split(r"([.@/+#])", tok):
        if not part:
            continue
        if part in SYMBOLS:
            out.append(SYMBOLS[part])
            continue
        for chunk in re.split(r"[_\-]+", part):
            if chunk.lower() in WORDS:
                out.append(WORDS[chunk.lower()])
            else:
                out.extend(_word(p) for p in _PIECES.findall(chunk))
    return " ".join(x for x in out if x)


# «استیجینگ (staging)»: the English gloss is for readers; spoken it would say the word twice
_LATIN_GLOSS = re.compile(r"\s*\((?=[^)]*[A-Za-z])[^()\u0600-\u06FF]{1,60}\)")


def speakable(text: str) -> str:
    text = _LATIN_GLOSS.sub("", text)
    return _TOKEN.sub(lambda m: _token(m.group(0)), text)


def for_voice(text: str, phonetic: bool = False) -> str:
    """Everything the voice needs before it speaks: the user's pronunciations first,
    then numbers as words, then English words in Persian letters.
    phonetic: a ParSiGo voice (fixes made of marks or OmniVoice-only swaps are left out)."""
    from . import numbers, pronounce

    return speakable(numbers.speak_numbers(pronounce.apply(text, phonetic)))
