"""Developer vocabulary: fixes the literal renderings a translation model
gives programming terms («interface» -> «رابط», «commit» -> «تعهد», …).

Each rule fires only when the SOURCE text contains the English term, and it
replaces the literal Persian rendering with the word Persian developers use.
Terms that also have an everyday meaning (branch, merge, client, …) fire
only in technical text (at least two developer words in the source).

The user can add rules in ~/Library/Application Support/Hamzaban/glossary.txt:

    english term = واژه‌ی دلخواه = ترجمه‌ی اشتباه ۱، ترجمه‌ی اشتباه ۲

(the third part is optional; without it, every rendering the model produced
for the term cannot be known, so the rule only fixes the listed ones)
"""
import re
import threading

from .config import APP_HOME

GLOSSARY_FILE = APP_HOME / "glossary.txt"
Z = "‌"
_L = "ء-يٱ-ۓ"

# (english regex, preferred Persian, literal renderings, only in technical text?)
BUILTIN = [
    (r"(?<!user )interfaces?", "اینترفیس", ["رابط کاربری", "رابط"], False),
    (r"autoloaders?", "اتولودر", ["خودکارلودر", "بارگذار خودکار", "بارگیرنده‌ی خودکار"], False),
    (r"autoload(?:ing|ed)?", "اتولود", ["بارگذاری خودکار", "بارگیری خودکار"], False),
    (r"commit(?:s|ted|ting)?", "کامیت", ["تعهد", "ثبت تغییرات", "ثبت"], True),
    (r"pull requests?|PRs?", "پول‌ریکوئست", ["درخواست کشش", "درخواست ادغام", "درخواست تغییرات", "درخواست تغییر"], True),
    (r"merg(?:e|ed|es|ing)", "مرج", ["ادغام"], True),
    (r"rebas(?:e|ed|ing)", "ریبیس", ["همگام‌سازی", "بازپایه‌گذاری"], True),
    (r"branch(?:es)?", "برنچ", ["شاخه"], True),
    (r"deploy(?:ed|ing|s|ment)?", "دیپلوی", ["مستقر", "استقرار"], True),
    (r"cach(?:e|ed|es|ing)", "کش", ["حافظه‌ی پنهان", "حافظه پنهان", "حافظه‌ی نهان", "حافظه نهان"], False),
    (r"hooks?", "هوک", ["قلاب"], True),
    (r"frameworks?", "فریم‌ورک", ["چارچوب"], True),
    (r"endpoints?", "اندپوینت", ["نقطه‌ی پایانی", "نقطه پایانی", "نقطه‌ی انتهایی", "نقطه انتهایی"], False),
    (r"containers?", "کانتینر", ["محفظه"], True),
    (r"repo(?:sitory|sitories|s)?", "ریپازیتوری", ["مخزن"], True),
    (r"hotfix(?:es)?", "هات‌فیکس", ["اصلاحیه‌ی فوری", "اصلاحیه فوری", "اصلاح سریع", "به‌روزرسانی فوری"], False),
    (r"namespaces?", "نیم‌اسپیس", ["فضای نام"], False),
    (r"methods?", "متد", ["روش"], True),
    (r"bugs?", "باگ", ["اشکال"], True),
    (r"lectures?", "جلسه", ["سخنرانی"], True),
    (r"staging", "استیجینگ", ["محیط آزمایشی", "محیط استیج"], True),
    (r"middleware", "میدل‌ور", ["میان‌افزار", "میان افزار"], False),
    (r"refactor(?:ed|ing|s)?", "ریفکتور", ["بازسازی", "بازنویسی"], True),
    (r"payloads?", "پی‌لود", ["محموله"], True),
    (r"callbacks?", "کال‌بک", ["فراخوانی برگشتی", "تابع بازگشتی"], False),
    (r"queries|query", "کوئری", ["پرس‌وجو", "پرس و جو"], True),
]

# words that make a text "technical"
_TECH_WORDS = re.compile(
    r"\b(api|apis|git|github|gitlab|commit|commits|repo|repository|pull request|deploy|deployment|docker|"
    r"kubernetes|database|sql|mysql|php|python|javascript|typescript|react|laravel|symfony|django|node\.?js|npm|"
    r"composer|frontend|backend|endpoint|json|html|css|runtime|debug|debugging|compile|namespace|interface|"
    r"interfaces|framework|middleware|localhost|codebase|refactor|staging|ci/cd|pipeline|autoload|hotfix|"
    r"programming|developer|developers|source code|stack trace|unit tests?|webhook|oop|ci|pr|prs|pull requests|rebase|rebased|rebasing|hotfix|feature branch|main branch|master branch)\b", re.I)

_lock = threading.Lock()
_rules = None
_mtime = None


def _compile(src, fa, wrong, tech_only):
    # a wrong rendering is replaced with its Persian endings kept (رابط‌ها -> اینترفیس‌ها)
    alts = "|".join(sorted((re.escape(w) for w in wrong if w), key=len, reverse=True))
    wrong_rx = re.compile(f"(?<![{_L}{Z}])(?:{alts})(?=[{Z} ]?(?:ها|های|هایی|ی|را|$)|[^{_L}{Z}]|$)") if alts else None
    return re.compile(rf"(?<![\w-])(?:{src})(?![\w-])", re.I), fa, wrong_rx, tech_only


def _user_rules():
    out = []
    try:
        lines = GLOSSARY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        parts = [p.strip() for p in line.split("=")]
        en, fa = parts[0], parts[1]
        wrong = [w.strip() for w in re.split(r"[،,]", parts[2])] if len(parts) > 2 else []
        if en and fa:
            out.append(_compile(re.escape(en), fa, wrong, False))
    return out


def rules():
    global _rules, _mtime
    try:
        mtime = GLOSSARY_FILE.stat().st_mtime
    except OSError:
        mtime = None
    with _lock:
        if _rules is None or mtime != _mtime:  # the user edited glossary.txt
            _rules = _user_rules() + [_compile(*r) for r in BUILTIN]
            _mtime = mtime
        return _rules


def is_technical(src: str) -> bool:
    return len(set(m.lower() for m in _TECH_WORDS.findall(src))) >= 2


def apply(src: str, fa: str) -> str:
    """Replace literal renderings of the developer terms found in `src`."""
    technical = None
    for en_rx, preferred, wrong_rx, tech_only in rules():
        if wrong_rx is None or not en_rx.search(src):
            continue
        if tech_only:
            if technical is None:
                technical = is_technical(src)
            if not technical:
                continue
        fa = wrong_rx.sub(preferred, fa)
    # «اینترفیس (اینترفیس)» after a replacement: drop the echo
    return re.sub(rf"(?<![{_L}{Z}])([{_L}{Z}]+) \(\1\)", r"\1", fa)
