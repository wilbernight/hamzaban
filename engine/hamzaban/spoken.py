"""Written Persian -> spoken Persian, for the dub voice only.

The translation is written Persian («می‌خواهم آن را ببینم، آیا ممکن است؟»); read
aloud it sounds like someone reading a text. Persian dubbing speaks the way people
talk («می‌خوام اونو ببینم، ممکنه؟»), which the voice also reads with a livelier
tune. Subtitles keep the written form.

Only unambiguous rules: a verb is changed only with a prefix that makes it a verb
(می‌/نمی‌/ب/ن) and only for present-tense stems whose spelling is not also a past
form («می‌کرد», «می‌خورد», «می‌خواند» stay), and «است» only at the end of a clause.
"""
import re

Z = "‌"
_W = r"[؀-ۿ‌]"            # a Persian letter (or ZWNJ) inside a word
_B = rf"(?<!{_W})"                        # word start
_E = rf"(?!{_W})"                         # word end
_MI = rf"(?:ن?می{Z}?|ب|ن)"               # verb prefixes

# present stems whose personal endings shorten in speech: stem -> spoken stem, and the
# spoken 3rd person singular ending (written «د»)
_STEMS = {
    "خواه": ("خوا", "د"),     # می‌خواهم -> می‌خوام, می‌خواهد -> می‌خواد
    "توان": ("تون", "ه"),     # می‌توانم -> می‌تونم, می‌تواند -> می‌تونه
    "دان": ("دون", "ه"),      # می‌دانم -> می‌دونم
    "گوی": ("گ", "ه"),        # می‌گویم -> می‌گم, می‌گوید -> می‌گه
    "رو": ("ر", "ه"),          # می‌روم -> می‌رم, می‌رود -> می‌ره
    "شو": ("ش", "ه"),          # می‌شود -> می‌شه
    "ده": ("د", "ه"),          # می‌دهم -> می‌دم, می‌دهد -> می‌ده
    "گذار": ("ذار", "ه"),     # می‌گذارم -> می‌ذارم
    "کن": ("کن", "ه"),        # می‌کند -> می‌کنه
    "زن": ("زن", "ه"),
    "بین": ("بین", "ه"),
    "رس": ("رس", "ه"),
    "گیر": ("گیر", "ه"),
    "کش": ("کش", "ه"),
    "پرس": ("پرس", "ه"),
    "نویس": ("نویس", "ه"),
    "ساز": ("ساز", "ه"),
    "افت": ("افت", "ه"),
    "فهم": ("فهم", "ه"),
    "گرد": ("گرد", "ه"),
    "خند": ("خند", "ه"),
    "شکن": ("شکن", "ه"),
}
_ENDINGS = {"م": "م", "ی": "ی", "یم": "یم", "ید": "ید", "ند": "ن"}

_LEXICON = [  # formal word -> everyday word (whole words)
    ("بسیار", "خیلی"), ("اکنون", "الان"), ("زیرا", "چون"), ("زیرا که", "چون"), ("چرا که", "چون"),
    ("همچنین", "همین‌طور"), ("سپس", "بعد"), ("هرگز", "هیچ‌وقت"), ("بنابراین", "پس"), ("چگونه", "چطور"),
    ("اگرچه", "با این‌که"), ("اینک", "حالا"), ("آن‌ها", "اونا"), ("آنها", "اونا"), ("آنجا", "اونجا"),
    ("آن‌جا", "اونجا"), ("این را", "اینو"), ("آن را", "اونو"), ("او را", "اونو"), ("آن", "اون"),
    ("او", "اون"), ("هستند", "هستن"), ("نیستند", "نیستن"), ("دارد", "داره"), ("ندارد", "نداره"),
    ("دارند", "دارن"), ("ندارند", "ندارن"), ("خانه", "خونه"), ("دیگر", "دیگه"), ("همان", "همون"),
    ("همین‌طور", "همین‌طور"), ("را", "رو"),
]
# «رفته است» is said «رفته»: perfect tenses drop «است» (adjectives ending the same way,
# «خسته است», «زنده است», keep it as «خسته‌ست»)
_PARTICIPLES = {
    "رفته", "گفته", "شده", "کرده", "آمده", "داده", "افتاده", "بوده", "داشته", "خواسته", "دیده", "نوشته",
    "گرفته", "ساخته", "زده", "کشته", "رسیده", "شنیده", "خورده", "مانده", "پرسیده", "فهمیده", "گذشته",
    "برگشته", "انجام‌شده", "شروع‌شده", "تمام‌شده", "یادگرفته", "فرستاده", "پیدا‌کرده", "گذاشته", "آورده",
}


def _verb(m):
    prefix, stem, ending = m.group(1), m.group(2), m.group(3)
    if stem == "ده" and prefix == "ب":  # «بدهی» is also "debt"
        return m.group(0)
    spoken, third = _STEMS[stem]
    if prefix.startswith(("می", "نمی")) and not prefix.endswith(Z):
        prefix += Z
    if ending == "د":
        return prefix + spoken + third
    return prefix + spoken + _ENDINGS[ending]


_VERBS = re.compile(_B + rf"({_MI})(" + "|".join(sorted(_STEMS, key=len, reverse=True))
                    + r")(یم|ید|ند|م|ی|د)" + _E)
_LEX = [(re.compile(_B + re.escape(a).replace(r"\ ", r"\s+") + _E), b) for a, b in _LEXICON]
_AYA = re.compile(r"(^|[.!?؟،:\n«]\s*)آیا\s+")
# «است» closing a clause (or before «که»): «خوب است.» -> «خوبه.»
_AST = re.compile(rf"({_W}+)\s+است(?=\s*(?:[.!?؟،;:»\n]|$|که{_E}))")


def _ast(m):
    w = m.group(1)
    if w in _PARTICIPLES:
        return w
    if w.endswith(("ا", "و")):
        return w + "ست"
    if w.endswith("ه"):
        return w + Z + "ست"
    return w + "ه"


def colloquial(text: str) -> str:
    """The way the sentence is said, for the voice («می‌خواهم» -> «می‌خوام»)."""
    out = _AYA.sub(r"\1", text)
    out = _VERBS.sub(_verb, out)
    for rx, b in _LEX:
        out = rx.sub(b, out)
    return _AST.sub(_ast, out)
