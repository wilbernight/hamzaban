"""Source-language detection and the names TranslateGemma expects."""
import re

# ISO 639-1 -> (English name for the prompt, Persian name for the UI)
NAMES = {
    "af": ("Afrikaans", "آفریکانس"), "am": ("Amharic", "امهری"), "ar": ("Arabic", "عربی"),
    "az": ("Azerbaijani", "آذربایجانی"), "be": ("Belarusian", "بلاروسی"), "bg": ("Bulgarian", "بلغاری"),
    "bn": ("Bengali", "بنگالی"), "bs": ("Bosnian", "بوسنیایی"), "ca": ("Catalan", "کاتالان"),
    "cs": ("Czech", "چکی"), "cy": ("Welsh", "ولزی"), "da": ("Danish", "دانمارکی"),
    "de": ("German", "آلمانی"), "el": ("Greek", "یونانی"), "en": ("English", "انگلیسی"),
    "eo": ("Esperanto", "اسپرانتو"), "es": ("Spanish", "اسپانیایی"), "et": ("Estonian", "استونیایی"),
    "eu": ("Basque", "باسکی"), "fa": ("Persian", "فارسی"), "fi": ("Finnish", "فنلاندی"),
    "fil": ("Filipino", "فیلیپینی"), "tl": ("Filipino", "فیلیپینی"), "fr": ("French", "فرانسوی"),
    "ga": ("Irish", "ایرلندی"), "gl": ("Galician", "گالیسیایی"), "gu": ("Gujarati", "گجراتی"),
    "he": ("Hebrew", "عبری"), "hi": ("Hindi", "هندی"), "hr": ("Croatian", "کرواتی"),
    "hu": ("Hungarian", "مجاری"), "hy": ("Armenian", "ارمنی"), "id": ("Indonesian", "اندونزیایی"),
    "is": ("Icelandic", "ایسلندی"), "it": ("Italian", "ایتالیایی"), "ja": ("Japanese", "ژاپنی"),
    "ka": ("Georgian", "گرجی"), "kk": ("Kazakh", "قزاقی"), "km": ("Khmer", "خمری"),
    "kn": ("Kannada", "کانارایی"), "ko": ("Korean", "کره‌ای"), "ku": ("Kurdish", "کردی"),
    "ky": ("Kyrgyz", "قرقیزی"), "lo": ("Lao", "لائوسی"), "lt": ("Lithuanian", "لیتوانیایی"),
    "lv": ("Latvian", "لتونیایی"), "mk": ("Macedonian", "مقدونی"), "ml": ("Malayalam", "مالایالم"),
    "mn": ("Mongolian", "مغولی"), "mr": ("Marathi", "مراتی"), "ms": ("Malay", "مالایی"),
    "mt": ("Maltese", "مالتی"), "my": ("Burmese", "برمه‌ای"), "nb": ("Norwegian", "نروژی"),
    "ne": ("Nepali", "نپالی"), "nl": ("Dutch", "هلندی"), "nn": ("Norwegian", "نروژی"),
    "no": ("Norwegian", "نروژی"), "pa": ("Punjabi", "پنجابی"), "pl": ("Polish", "لهستانی"),
    "ps": ("Pashto", "پشتو"), "pt": ("Portuguese", "پرتغالی"), "ro": ("Romanian", "رومانیایی"),
    "ru": ("Russian", "روسی"), "si": ("Sinhala", "سینهالی"), "sk": ("Slovak", "اسلواکی"),
    "sl": ("Slovenian", "اسلوونیایی"), "sq": ("Albanian", "آلبانیایی"), "sr": ("Serbian", "صربی"),
    "sv": ("Swedish", "سوئدی"), "sw": ("Swahili", "سواحیلی"), "ta": ("Tamil", "تامیلی"),
    "te": ("Telugu", "تلوگو"), "tg": ("Tajik", "تاجیکی"), "th": ("Thai", "تایلندی"),
    "tr": ("Turkish", "ترکی استانبولی"), "uk": ("Ukrainian", "اوکراینی"), "ur": ("Urdu", "اردو"),
    "uz": ("Uzbek", "ازبکی"), "vi": ("Vietnamese", "ویتنامی"), "zh": ("Chinese", "چینی"),
    "zu": ("Zulu", "زولو"), "yue": ("Cantonese", "کانتونی"), "la": ("Latin", "لاتین"),
}

_URLISH = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|\d+")

_SCRIPT_GUESS = [  # (regex, code) for when the statistical detector is missing
    (re.compile(r"[぀-ヿ]"), "ja"), (re.compile(r"[가-힯]"), "ko"),
    (re.compile(r"[一-鿿]"), "zh"), (re.compile(r"[Ѐ-ӿ]"), "ru"),
    (re.compile(r"[֐-׿]"), "he"), (re.compile(r"[Ͱ-Ͽ]"), "el"),
    (re.compile(r"[฀-๿]"), "th"), (re.compile(r"[ऀ-ॿ]"), "hi"),
    (re.compile(r"[؀-ۿ]"), "ar"), (re.compile(r"[԰-֏]"), "hy"),
    (re.compile(r"[Ⴀ-ჿ]"), "ka"),
]

def detect(text: str) -> str:
    """Best-guess ISO code of `text` ('en' when there is nothing to go on)."""
    sample = _URLISH.sub(" ", text[:3000])
    try:
        import py3langid

        code, _score = py3langid.classify(sample)
        return code
    except Exception:
        for rx, code in _SCRIPT_GUESS:
            if len(rx.findall(sample)) > 10:
                return code
        return "en"


def name_en(code: str) -> str:
    return NAMES.get(code, NAMES.get(code.split("-")[0], (code, code)))[0]


def name_fa(code: str) -> str:
    return NAMES.get(code, NAMES.get(code.split("-")[0], (code, code)))[1]
