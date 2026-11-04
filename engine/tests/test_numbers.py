"""Model-free tests for hamzaban.numbers: run with  python3 -m unittest discover engine/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/: hamzaban + workers

from hamzaban import persian  # noqa: E402
from hamzaban.numbers import (digits_words, latin_number, number_words, ordinal_words,  # noqa: E402
                              speak_numbers)

Z = "‌"


class NumberWordsTests(unittest.TestCase):
    def test_small_numbers(self):
        for n, w in [(0, "صفر"), (1, "یک"), (10, "ده"), (11, "یازده"), (19, "نوزده"), (20, "بیست"),
                     (21, "بیست و یک"), (99, "نود و نه"), (100, "صد"), (101, "صد و یک"), (200, "دویست"),
                     (999, "نهصد و نود و نه")]:
            self.assertEqual(number_words(n), w)

    def test_thousands_millions_billions(self):
        for n, w in [(1000, "هزار"), (1001, "هزار و یک"), (4500, "چهار هزار و پانصد"),
                     (2024, "دو هزار و بیست و چهار"), (1403, "هزار و چهارصد و سه"),
                     (100_000, "صد هزار"), (1_000_000, "یک میلیون"),
                     (1_250_000, "یک میلیون و دویست و پنجاه هزار"), (1_001_000, "یک میلیون و هزار"),
                     (2_000_000_000, "دو میلیارد")]:
            self.assertEqual(number_words(n), w)
        self.assertEqual(number_words(999_999_999_999),
                         "نهصد و نود و نه میلیارد و نهصد و نود و نه میلیون و نهصد و نود و نه هزار و نهصد و نود و نه")

    def test_negative(self):
        self.assertEqual(number_words(-15), "منفی پانزده")

    def test_ordinals(self):
        for n, w in [(1, "یکم"), (2, "دوم"), (3, "سوم"), (4, "چهارم"), (20, "بیستم"), (23, "بیست و سوم"),
                     (30, f"سی{Z}ام"), (100, "صدم"), (1000, "هزارم")]:
            self.assertEqual(ordinal_words(n), w)

    def test_digit_by_digit_helpers(self):
        self.assertEqual(digits_words("۰۹۱۲"), "صفر نه یک دو")
        self.assertEqual(digits_words("305"), "سه صفر پنج")
        self.assertEqual(latin_number("3"), "سه")
        self.assertEqual(latin_number("2024"), "دو هزار و بیست و چهار")
        self.assertEqual(latin_number("007"), "صفر صفر هفت")
        self.assertEqual(latin_number("1234567890123"), "یک دو سه چهار پنج شش هفت هشت نه صفر یک دو سه")
        self.assertEqual(latin_number("abc"), "abc")


class SpeakNumbersTests(unittest.TestCase):
    def say(self, text, expected):
        out = speak_numbers(text)
        self.assertEqual(out, expected)
        self.assertNotIn("  ", out)

    def test_integers_any_digits_and_separators(self):
        self.say("۴۵۰۰ تومان", "چهار هزار و پانصد تومان")
        self.say("۱۰۰ نفر", "صد نفر")
        self.say("٤٥٠٠ و 4500", "چهار هزار و پانصد و چهار هزار و پانصد")
        for s in ("4,500", "۴٬۵۰۰", "۴,۵۰۰"):
            self.say(f"قیمت {s} است", "قیمت چهار هزار و پانصد است")
        self.say("۱٫۰۰۰٫۰۰۰ یورو", "یک میلیون یورو")

    def test_decimals(self):
        self.say("۳٫۵ میلیون", "سه و نیم میلیون")
        self.say("3.5", "سه و نیم")
        self.say("۱۲٫۵۰", "دوازده و نیم")
        self.say("۰٫۵", "نیم")
        self.say("عدد ۳٫۱۴", "عدد سه ممیز چهارده")
        self.say("۰٫۰۵", "صفر ممیز صفر پنج")

    def test_percent_before_or_after(self):
        for s in ("۸٪", "٪۸", "8%", "۸ ٪"):
            self.say(f"تخفیف {s} است", "تخفیف هشت درصد است")
        self.say("۲۰-۳۰٪", "بیست تا سی درصد")

    def test_currency(self):
        self.say("$۵ و ۵۰€ و £۳", "پنج دلار و پنجاه یورو و سه پوند")
        self.say("۴٬۵۰۰ $ هزینه", "چهار هزار و پانصد دلار هزینه")
        self.say("۵$را", "پنج دلار را")

    def test_times(self):
        self.say("ساعت ۱۰:۳۰", "ساعت ده و سی دقیقه")
        self.say("ساعت ۱۰:۰۰", "ساعت ده")
        self.say("ساعت ۰۸:۰۵", "ساعت هشت و پنج دقیقه")
        self.say("۴۵:۱۲", "چهل و پنج دقیقه و دوازده ثانیه")

    def test_ranges_only_between_two_numbers(self):
        self.say("۲-۳ روز", "دو تا سه روز")
        self.say("۲–۳ روز", "دو تا سه روز")
        self.say("۲۰۲۰ – ۲۰۲۴", "دو هزار و بیست تا دو هزار و بیست و چهار")
        self.say("۱۰:۰۰–۱۲:۳۰", "ده تا دوازده و سی دقیقه")
        self.say("۲-بعدی", "دو-بعدی")

    def test_ordinals_and_decades(self):
        self.say("۴م و ۴ام", "چهارم و چهارم")
        self.say("۳ام و ۱ام و ۲۰ام", "سوم و یکم و بیستم")
        self.say("۴مین جلسه", "چهارمین جلسه")
        self.say("۳۰ام", f"سی{Z}ام")
        self.say(f"دهه{Z}ی ۵۰{Z}ها و ۵۰ها", f"دهه{Z}ی پنجاه{Z}ها و پنجاه{Z}ها")
        self.say("۴ماه", f"چهار{Z}ماه")

    def test_codes_digit_by_digit(self):
        self.say("شماره ۰۹۱۲۳۴۵۶۷۸۹", "شماره صفر نه یک دو سه چهار پنج شش هفت هشت نه")
        self.say("کد ۱۲۳۴۵۶۷۸۹۰۱۲۳", "کد یک دو سه چهار پنج شش هفت هشت نه صفر یک دو سه")
        self.say("۰۹۱۲-۳۴۵-۶۷۸۹", "صفر نه یک دو، سیصد و چهل و پنج، شش هفت هشت نه")

    def test_years_are_plain_integers(self):
        self.say("سال ۱۴۰۳", "سال هزار و چهارصد و سه")
        self.say("سال ۲۰۲۶", "سال دو هزار و بیست و شش")
        self.say("۱۴۰۳/۰۵/۱۲", "دوازدهم مرداد هزار و چهارصد و سه")

    def test_versions_fractions_and_signs(self):
        self.say("PHP ۸٫۱ و Python 3.12", "PHP هشت نقطه یک و Python سه نقطه دوازده")
        self.say(f"نسخه{Z}ی ۱٫۲٫۳", f"نسخه{Z}ی یک نقطه دو نقطه سه")
        self.say("۳/۴ کیک", "سه چهارم کیک")
        self.say("۱۶:۹ و ۱۹۲۰×۱۰۸۰", "شانزده به نه و هزار و نهصد و بیست در هزار و هشتاد")
        self.say("دمای -۵ درجه", "دمای منفی پنج درجه")
        self.say("۲۵°C", f"بیست و پنج درجه{Z}ی سانتی{Z}گراد")
        self.say("فصل۳", "فصل سه")

    def test_lone_symbols_and_plain_text_unchanged(self):
        for s in ("٪ و $ تنها", "متن بدون عدد  با دو فاصله", ""):
            self.assertEqual(speak_numbers(s), s)

    def test_latin_tokens_urls_and_emails_untouched(self):
        for s in ("Q3", "v2.1", "mp3", "iPhone15", "15GB", "3pm", "$5M",
                  "https://example.com/page/2024", "www.site.com/1", "user123@mail.com"):
            self.assertEqual(speak_numbers(f"این {s} است"), f"این {s} است")

    def test_after_normalize(self):
        text = persian.normalize("فاکتور شماره 1057 تا 30 سپتامبر، ساعت 10:00، تخفیف 8%")
        self.assertEqual(speak_numbers(text), "فاکتور شماره هزار و پنجاه و هفت تا سی‌ام سپتامبر، ساعت ده، تخفیف هشت درصد")


if __name__ == "__main__":
    unittest.main()
