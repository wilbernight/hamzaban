"""Model-free tests: run with  python3 -m unittest discover engine/tests"""
import sys
import unittest


def setUpModule():
    """Tests never see (or write) the user's real saved fixes."""
    from unittest import mock
    from hamzaban import idioms, pronounce, server

    global _isolation
    nowhere = Path("/nonexistent/hamzaban-test")
    _isolation = [mock.patch.object(pronounce, "WORDS_FILE", nowhere / "p.txt"),
                  mock.patch.object(idioms, "WORDS_FILE", nowhere / "i.txt"),
                  mock.patch.object(pronounce, "PRONOUNCE_FILE", nowhere / "user-p.txt"),
                  mock.patch.object(idioms, "IDIOMS_FILE", nowhere / "user-i.txt"),
                  mock.patch.object(server, "_code_copies", lambda kind: [])]
    for m in _isolation:
        m.start()
    pronounce._cache["rx"] = None
    idioms._cache["rules"] = None


def tearDownModule():
    for m in _isolation:
        m.stop()
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/: hamzaban + workers

from hamzaban import persian  # noqa: E402
from hamzaban.config import PERSIAN_WORDS  # noqa: E402
from hamzaban import glossary  # noqa: E402
from hamzaban.speech import speakable  # noqa: E402
from hamzaban.asr import to_sentences, words_to_sentences  # noqa: E402
from hamzaban.dubbing import OVERLAP, Region, covered_until, next_window  # noqa: E402
from hamzaban.translator import MAX_PIECE_CHARS, needs_translation, plan_pieces, prompt_for  # noqa: E402

Z = "‌"


class PersianTests(unittest.TestCase):
    def test_letter_forms_and_punctuation(self):
        self.assertEqual(persian.normalize("كتاب علي , خوب است ?"), "کتاب علی، خوب است؟")

    def test_zwnj_prefix_and_suffixes(self):
        self.assertEqual(persian.normalize("ما می خواهیم کتاب ها را بزرگ تر کنیم"),
                         f"ما می{Z}خواهیم کتاب{Z}ها را بزرگ{Z}تر کنیم")
        self.assertEqual(persian.normalize("او نمی داند"), f"او نمی{Z}داند")

    def test_words_ending_in_mi_are_left_alone(self):
        self.assertEqual(persian.normalize("نامه ی رسمی ارسال شد"), f"نامه{Z}ی رسمی ارسال شد")
        self.assertEqual(persian.normalize("کمی صبر کنید"), "کمی صبر کنید")

    def test_urls_and_emails_untouched(self):
        s = "لطفا به https://example.com/a?b=1,2 سر بزنید یا به a.b@mail.com بنویسید"
        out = persian.normalize(s)
        self.assertIn("https://example.com/a?b=1,2", out)
        self.assertIn("a.b@mail.com", out)

    def test_persian_digits_only_for_free_numbers(self):
        self.assertEqual(persian.normalize("فاکتور شماره 1057 تا 30 سپتامبر، ساعت 10:00، تخفیف 8%"),
                         "فاکتور شماره ۱۰۵۷ تا ۳۰ سپتامبر، ساعت ۱۰:۰۰، تخفیف ۸٪")
        self.assertEqual(persian.normalize("گزارش Q3 و نسخه‌ی v2.1 و مقدار 3.5"),
                         "گزارش Q3 و نسخه‌ی v2.1 و مقدار ۳٫۵")
        self.assertIn("https://suivi.example.fr/48213", persian.normalize("آدرس: https://suivi.example.fr/48213"))

    def test_stray_script_letters_inside_persian_words(self):
        self.assertEqual(persian.normalize("کاتрин ویبر"), "کاترین ویبر")
        self.assertEqual(persian.normalize("Anna Müller از Northwind"), "Anna Müller از Northwind")
        self.assertEqual(persian.normalize(f"همه‌ی API{Z}ها"), f"همه‌ی API{Z}ها")

    def test_compounds_from_the_persian_writing_rules(self):
        cases = {
            "گزارش سه ماهه ی ما روز سه شنبه آماده است":
                f"گزارش سه{Z}ماهه{Z}ی ما روز سه{Z}شنبه آماده است",
            "لطفا این نرم افزار را نصب کنید": f"لطفاً این نرم{Z}افزار را نصب کنید",
            "فارغ التحصیلان دانشگاه همه چیز را دیدند": f"فارغ{Z}التحصیلان دانشگاه همه{Z}چیز را دیدند",
            "آن ها رفته اند و خانه اش خالی است": f"آن{Z}ها رفته{Z}اند و خانه{Z}اش خالی است",
            # plain word pairs stay apart, whatever the word list says
            "این کار که در آن را شده است": "این کار که در آن را شده است",
        }
        for src, want in cases.items():
            self.assertEqual(persian.normalize(src), want)

    @unittest.skipUnless(PERSIAN_WORDS.exists(), "persian-writing word list not installed")
    def test_dictionary_checked_heads(self):
        self.assertEqual(persian.normalize("تجربه ای شگفت انگیز، بی نظیر و بی سابقه؛ امکان پذیر نیست"),
                         f"تجربه{Z}ای شگفت{Z}انگیز، بی{Z}نظیر و بی{Z}سابقه؛ امکان{Z}پذیر نیست")
        self.assertEqual(persian.normalize("یک ساز خرید"), "یک ساز خرید")

    def test_looks_persian(self):
        self.assertTrue(persian.looks_persian("سلام، امیدوارم حالتان خوب باشد. گزارش را فرستادم."))
        self.assertFalse(persian.looks_persian("مرحبا، كيف حالك؟ أتمنى أن تكون بخير في هذه الأيام."))
        self.assertFalse(persian.looks_persian("Hello, I hope you are doing well."))


class DeveloperTermsTests(unittest.TestCase):
    def test_literal_renderings_are_replaced(self):
        src = "Please merge the PR after the CI passes. The interfaces live in the cache folder."
        fa = "لطفاً پس از موفقیت CI، درخواست ادغام را ادغام کنید. رابط‌ها در پوشه‌ی حافظه پنهان هستند."
        self.assertEqual(glossary.apply(src, fa),
                         "لطفاً پس از موفقیت CI، پول‌ریکوئست را مرج کنید. اینترفیس‌ها در پوشه‌ی کش هستند.")

    def test_everyday_words_stay_in_non_technical_text(self):
        # "branch" of a bank is not a git branch; «رابطه» is not «رابط»
        self.assertEqual(glossary.apply("Visit our new branch downtown.", "از شاخه‌ی جدید ما دیدن کنید."),
                         "از شاخه‌ی جدید ما دیدن کنید.")
        self.assertEqual(glossary.apply("The interface of our relationship", "رابطه‌ی ما"), "رابطه‌ی ما")

    def test_speakable_code_names(self):
        self.assertEqual(speakable("فایل PaymentGateway.php را باز کنید"), "فایل پیمنت گیت‌وی دات پی‌اچ‌پی را باز کنید")
        self.assertEqual(speakable("دستور composer dump-autoload"), "دستور کامپوزر دامپ اتولود")
        self.assertEqual(speakable("از API و stanford.edu"), f"از ای{Z}پی{Z}آی و استنفورد دات ای{Z}دی{Z}یو")
        self.assertEqual(speakable("در استیجینگ (staging) دیپلوی کنید"), "در استیجینگ دیپلوی کنید")

    def test_ui_interface_and_echoes(self):
        self.assertEqual(glossary.apply("Create a PHP interface.", "یک رابط کاربری (رابط) در PHP بسازید."),
                         "یک اینترفیس در PHP بسازید.")
        self.assertEqual(glossary.apply("Redesign the user interface.", "رابط کاربری را بازطراحی کنید."),
                         "رابط کاربری را بازطراحی کنید.")
        self.assertEqual(glossary.apply("I pushed a hotfix to the main branch and rebased.", "به شاخه اصلی"),
                         "به برنچ اصلی")


class OmniDurationTests(unittest.TestCase):
    def test_slots_shorten_but_never_over_squeeze(self):
        from omni_worker import plan_durations

        natural = [4.0, 4.0, 4.0, 0.0, 3.0]
        slots = [5.0, 3.5, 1.0, 2.0, None]
        self.assertEqual(plan_durations(natural, slots, max_squeeze=1.35),
                         [4.0, 3.5, 4.0 / 1.35, None, 3.0])
        self.assertEqual(plan_durations([2.0], None), [2.0])


class PlanTests(unittest.TestCase):
    def test_passthrough_and_grouping(self):
        text = "Hi Sara,\n\nThanks for the report.\n\nhttps://x.io/r\n\n--\nJohn"
        pieces = plan_pieces(text)
        self.assertEqual(pieces[0], ("Hi Sara,\n\nThanks for the report.", True, ""))
        self.assertEqual(pieces[1], ("https://x.io/r", False, "\n\n"))
        self.assertEqual(pieces[2], ("--\nJohn", True, "\n\n"))
        self.assertFalse(needs_translation("سلام، این متن فارسی است و ترجمه نمی‌خواهد."))

    def test_long_paragraph_is_split_on_sentences(self):
        para = " ".join(f"This is sentence number {i} of a long paragraph." for i in range(80))
        pieces = plan_pieces(para)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p) <= MAX_PIECE_CHARS * 1.5 for p, _, _ in pieces))
        self.assertEqual([sep for _, _, sep in pieces][1:], [" "] * (len(pieces) - 1))
        self.assertEqual(" ".join(p for p, _, _ in pieces), para)

    def test_prompt_format(self):
        p = prompt_for("Hello", "de")
        self.assertTrue(p.startswith("You are a professional German (de) to Persian (fa-IR) translator."))
        self.assertTrue(p.endswith("into Persian:\n\n\nHello"))


class DubPlanTests(unittest.TestCase):
    def test_covered_until_chains_regions(self):
        regs = [Region(10, 20), Region(0, 10), Region(25, 30)]
        self.assertEqual(covered_until(regs, 3), 20)
        self.assertEqual(covered_until(regs, 22), 22)
        self.assertEqual(covered_until(regs, 26), 30)

    def test_next_window_drops_cut_sentence(self):
        segs = [{"start": 0.5, "end": 9.0, "text": "a"}, {"start": 9.5, "end": 28.0, "text": "b"},
                {"start": 28.5, "end": 34.9, "text": "c"}]
        keep, nxt = next_window(100.0, segs, 30.0, 30.0 + OVERLAP, at_end=False)
        self.assertEqual([s["text"] for s in keep], ["a", "b"])
        self.assertEqual(nxt, 128.0)

    def test_next_window_silence_and_end(self):
        self.assertEqual(next_window(0.0, [], 30.0, 35.0, False), ([], 30.0))
        keep, nxt = next_window(50.0, [{"start": 1, "end": 3, "text": "x"}], 30.0, 12.0, at_end=True)
        self.assertEqual((len(keep), nxt), (1, 62.0))

    def test_words_become_sentences(self):
        words = [(0.0, 0.4, " This"), (0.4, 0.8, " is"), (0.8, 1.2, " the"), (1.2, 1.9, " closest."),
                 (2.0, 2.3, " Mr."), (2.3, 2.8, " Jobs"), (2.8, 3.3, " spoke."),
                 (4.5, 5.0, " Then"), (5.0, 5.4, " silence")]
        out = words_to_sentences([{"start": a, "end": b, "word": w} for a, b, w in words])
        self.assertEqual([s["text"] for s in out], ["This is the closest.", "Mr. Jobs spoke.", "Then silence"])
        self.assertEqual((out[1]["start"], out[1]["end"]), (2.0, 3.3))

    def test_sentences_merge_until_punctuation(self):
        segs = [{"start": 0, "end": 2, "text": "So today we"}, {"start": 2.1, "end": 4, "text": "talk about AI."},
                {"start": 4.2, "end": 6, "text": "Next topic"}]
        out = to_sentences(segs)
        self.assertEqual([s["text"] for s in out], ["So today we talk about AI.", "Next topic"])
        self.assertEqual((out[0]["start"], out[0]["end"]), (0, 4))


class HeatTests(unittest.TestCase):
    """On a hot Mac the dub prepares less far ahead and rests the GPU between batches."""

    def _job(self):
        import tempfile
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return DubJob(SimpleNamespace(cache_dir=Path(tmp.name)), "https://example.com/v", None,
                      "omni_female", 0, "m")

    def test_macos_reports_a_level(self):
        from hamzaban import thermal

        self.assertIn(thermal.level(), range(4))

    def test_hot_mac_shortens_lookahead_and_rests(self):
        import time
        from unittest import mock
        from hamzaban import dubbing
        from hamzaban.dubbing import HOT_LOOKAHEAD, LOOKAHEAD

        job = self._job()
        with mock.patch.object(dubbing.thermal, "hot", return_value=False):
            self.assertEqual(job.lookahead(), LOOKAHEAD)
            t = time.monotonic(); job._rest_if_hot(5.0)
            self.assertLess(time.monotonic() - t, 0.1)            # cool: never waits
            self.assertEqual(job.poll(0)["lead_cap"], LOOKAHEAD)
        with mock.patch.object(dubbing.thermal, "hot", return_value=True):
            self.assertEqual(job.lookahead(), HOT_LOOKAHEAD)
            self.assertTrue(job.poll(0)["hot"])
            t = time.monotonic(); job._rest_if_hot(5.0)
            self.assertLess(time.monotonic() - t, 0.1)            # little ready: keep working
            job.regions.append(Region(0, 60))                     # 60 s ready: rest
            t = time.monotonic(); job._rest_if_hot(0.6)
            self.assertGreater(time.monotonic() - t, 0.5)
            job.stop()                                            # stopping ends the rest at once
            t = time.monotonic(); job._rest_if_hot(5.0)
            self.assertLess(time.monotonic() - t, 0.1)


class DubPipelineTests(unittest.TestCase):
    """The dub workers with fake models: nothing is skipped at the start, a failed voice call is retried."""

    def _job(self, t0, tts=None, cache_dir=None, voice="omni_female"):
        import tempfile
        import numpy as np
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob
        from hamzaban.dubcache import AsrCache, TtsCache

        if cache_dir is None:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            cache_dir = Path(tmp.name)
        reads = []

        class Source:
            duration, title, local_file, key = 300.0, "", None, "Test:v1"

            def read(self, start, seconds, sr):
                reads.append((start, seconds))
                return np.zeros(int(seconds * sr), dtype=np.float32)

            def start_background_download(self):
                pass

        asr = SimpleNamespace(transcribe=lambda audio, lang, prompt=None: ([{"start": 0.5, "end": 4.0, "text": "Hello there."}], "en"))
        engine = SimpleNamespace(cache_dir=cache_dir, asr=asr, tts=tts, cfg={"default_voice": "male_news"},
                                 asr_cache=AsrCache(cache_dir / "asr", "w"), tts_cache=TtsCache(cache_dir / "tts"))
        job = DubJob(engine, "https://example.com/v", None, voice, t0, "m")
        job.source = Source()
        return job, reads

    def _run(self, fn, job, until, timeout=5.0):
        import threading
        import time

        th = threading.Thread(target=fn, daemon=True)
        th.start()
        deadline = time.time() + timeout
        while time.time() < deadline and not until():
            time.sleep(0.02)
        job.stop()
        th.join(timeout=3)

    def test_first_window_starts_before_the_playhead(self):
        from hamzaban.dubbing import FIRST_WINDOW, LEAD_IN

        job, reads = self._job(40.0)
        self._run(job._asr_loop, job, lambda: len(job.regions) >= 1)
        start, seconds = reads[0]
        self.assertEqual(start, 40.0 - LEAD_IN)                  # the sentence in progress is caught whole
        self.assertGreaterEqual(start + seconds, 40.0 + FIRST_WINDOW)  # and as much lies ahead as before
        job2, reads2 = self._job(3.0)
        self._run(job2._asr_loop, job2, lambda: len(job2.regions) >= 1)
        self.assertEqual(reads2[0][0], 0.0)                      # never before the start of the video

    def test_failed_speech_is_retried(self):
        import numpy as np
        from types import SimpleNamespace
        from hamzaban.dubbing import Sentence

        calls = []

        def synth_many(texts, voice, slots=None, ref=None):
            calls.append(len(texts))
            if len(calls) == 1:
                raise RuntimeError("worker restarted")
            return [np.full(24000, 0.1, dtype=np.float32) for _ in texts]

        tts = SimpleNamespace(batch_size=lambda v: 1, synth_many=synth_many, sample_rate=24000,
                              fit=lambda a, s, max_speedup: a)
        job, _ = self._job(0.0, tts)
        region = Region(0, 10)
        sent = Sentence(1, 1.0, 3.0, "Hi.", region)
        sent.fa, sent.state = "سلام.", "tts"
        job.sents[1], job.regions = sent, [region]
        region.sids.append(1)
        with self.assertLogs("hamzaban.dub", "ERROR"):
            self._run(job._tts_loop, job, lambda: sent.state == "ready")
        self.assertEqual(calls, [1, 1])
        self.assertAlmostEqual(sent.dur, 1.0)

    def _tts_job(self, cache_dir, calls):
        import numpy as np
        from types import SimpleNamespace
        from hamzaban.dubbing import Sentence

        def synth_many(texts, voice, slots=None, ref=None):
            calls.append(len(texts))
            return [np.full(24000, 0.1, dtype=np.float32) for _ in texts]

        tts = SimpleNamespace(batch_size=lambda v: 1, synth_many=synth_many, sample_rate=24000,
                              fit=lambda a, s, max_speedup: a)
        job, _ = self._job(0.0, tts, cache_dir)
        region = Region(0, 10)
        sent = Sentence(1, 1.0, 3.0, "Hi.", region)
        sent.fa, sent.state = "سلام.", "tts"
        job.sents[1], job.regions = sent, [region]
        region.sids.append(1)
        self._run(job._tts_loop, job, lambda: sent.state == "ready")
        return job, sent

    def test_second_dub_of_a_video_reuses_asr_and_speech(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            job, reads = self._job(0.0, cache_dir=d)
            self._run(job._asr_loop, job, lambda: len(job.regions) >= 3)
            self.assertTrue(reads)
            job2, reads2 = self._job(0.0, cache_dir=d)      # e.g. the same video with another voice
            self._run(job2._asr_loop, job2, lambda: len(job2.regions) >= 3)
            self.assertEqual(reads2, [])                       # nothing listened to again
            self.assertEqual([(s.start, s.src) for s in job2.sents.values()][:3],
                             [(s.start, s.src) for s in job.sents.values()][:3])
            job3, reads3 = self._job(400.0, cache_dir=d)      # not heard before: Whisper runs
            job3.source.duration = 900.0
            self._run(job3._asr_loop, job3, lambda: len(job3.regions) >= 1)
            self.assertTrue(reads3)
            calls = []
            _, s1 = self._tts_job(d, calls)
            _, s2 = self._tts_job(d, calls)
            self.assertEqual(calls, [1])                       # spoken once, reused the second time
            self.assertAlmostEqual(s2.dur, s1.dur)

    def test_cached_region_is_cut_to_fit_existing_ones(self):
        job, _ = self._job(10.0)
        job.regions = [Region(0, 10), Region(20, 50)]
        known = [{"a": 0, "b": 40, "lang": "en", "sents": [
            {"start": 5, "end": 9, "text": "a"}, {"start": 12, "end": 15, "text": "b"},
            {"start": 25, "end": 30, "text": "c"}]}]
        with job.cv:
            self.assertTrue(job._use_cached(known, 10.0))
        r = job.regions[-1]
        self.assertEqual((r.a, r.b), (10, 20))
        self.assertEqual([job.sents[i].src for i in r.sids], ["b"])  # only what no region had yet


class WorkerReleaseTests(unittest.TestCase):
    def test_idle_worker_quits_busy_one_stays(self):
        import subprocess
        from hamzaban.workers import VoiceWorker

        w = VoiceWorker("t", 1, "/bin/sleep", "/bin/sleep")
        w._proc = subprocess.Popen(["/bin/sleep", "60"])
        w._busy = 1
        self.assertFalse(w.stop_if_idle())
        self.assertIsNone(w._proc.poll())
        w._busy = 0
        proc = w._proc
        self.assertTrue(w.stop_if_idle())
        self.assertIsNotNone(proc.poll())

    def test_only_the_other_voices_are_released(self):
        from unittest import mock
        from hamzaban import tts

        with mock.patch.object(tts.omni, "worker") as om, mock.patch.object(tts.aava, "worker") as av:
            om.stop_if_idle.return_value = av.stop_if_idle.return_value = True
            tts.release_other_voices("omni_male")
            om.stop_if_idle.assert_not_called(); av.stop_if_idle.assert_called_once()
            om.reset_mock(); av.reset_mock()
            tts.release_other_voices("aava_female")
            om.stop_if_idle.assert_called_once(); av.stop_if_idle.assert_not_called()
            om.reset_mock(); av.reset_mock()
            tts.release_other_voices("male_news")
            om.stop_if_idle.assert_called_once(); av.stop_if_idle.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class YoutubeCookieTests(unittest.TestCase):
    def test_only_youtube_cookies_reach_yt_dlp_and_the_file_is_private_and_removed(self):
        import os
        import stat
        import tempfile
        from hamzaban.media import clean_cookies, cookie_file

        raw = [{"domain": ".youtube.com", "path": "/", "secure": True, "hostOnly": False, "expires": 2000000000,
                "name": "SID", "value": "abc"},
               {"domain": "www.youtube.com", "path": "/", "secure": True, "hostOnly": True, "expires": 0,
                "name": "PREF", "value": "x"},
               {"domain": ".google.com", "path": "/", "name": "SID", "value": "nope"},
               {"domain": "evil-youtube.com", "path": "/", "name": "a", "value": "b"},
               {"domain": ".youtube.com", "path": "/", "name": "bad", "value": "a\tb"}]
        cookies = clean_cookies(raw)
        self.assertEqual([c["name"] for c in cookies], ["SID", "PREF"])
        with tempfile.TemporaryDirectory() as d:
            with cookie_file(cookies, Path(d)) as path:
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
                lines = Path(path).read_text().splitlines()
                self.assertEqual(lines[1].split("\t"), [".youtube.com", "TRUE", "/", "TRUE", "2000000000", "SID", "abc"])
                self.assertEqual(lines[2].split("\t")[:2], ["www.youtube.com", "FALSE"])
            self.assertFalse(Path(path).exists())
            with cookie_file([], Path(d)) as none:
                self.assertIsNone(none)


class VoiceTextTests(unittest.TestCase):
    """What the voice is given: no digits, no Latin letters, known hard words hinted."""

    def test_numbers_dimensions_english_and_hints(self):
        from hamzaban.speech import english, for_voice

        out = for_voice("این کد با رزولوشن (28x28 پیکسل) و ۳ لایه، یعنی input layer کار می‌کند.")
        self.assertIn("بیست و هشت در بیست و هشت", out)
        self.assertIn("سه لایه", out)
        self.assertIn("کُد", out)
        self.assertNotRegex(out, r"[A-Za-z0-9۰-۹]")
        self.assertEqual(for_voice("بین ۰ و ۱۰"), "بین صفر و ده")
        self.assertEqual(for_voice("API و PHP"), "ای‌پی‌آی و پی‌اچ‌پی")  # acronyms are spelled
        self.assertNotRegex(english("Zorblaxian"), r"[A-Za-z]")       # unknown words still get letters

    def test_english_words_from_the_pronouncing_dictionary(self):
        from hamzaban import speech

        if not speech._cmudict():
            self.skipTest("cmudict not installed")
        for w, fa in {"network": "نتورک", "pixel": "پیکسل", "function": "فانکشن", "error": "ارور",
                      "stream": "استریم", "react": "ریاکت", "value": "ولیو"}.items():
            self.assertEqual(speech.english(w), fa)

    def test_repeated_words_are_phonemised_once(self):
        from hamzaban.pronounce import phonemise_repeats, split_repeats
        from hamzaban.tts import PersianTTS

        self.assertEqual(split_repeats("سلام دنیا"), [("سلام دنیا", 1)])
        self.assertEqual(split_repeats("تیم ما\nدبلیو دبلیو دبلیو دات کام"),
                         [("تیم ما", 1), ("دبلیو", 3), ("دات کام", 1)])
        self.assertEqual(split_repeats("نه، نه، نه!"), [("نه", 3)])

        g2p = type("G2P", (), {"phonemise": lambda self, t, keep_ezafe=False: "<" + t.replace(" ", "_") + ">"})()
        eng = type("Eng", (), {"_g2p": g2p})()
        PersianTTS._hook_g2p(eng)
        self.assertEqual(eng._g2p.phonemise("بله بله بله دات کام"),
                         "<بله> <بله> <بله> <دات_کام>")  # no word-for-word match: piece by piece

        def g2p(t):  # a word-for-word fake G2P with an ezafe on the first word
            ws = t.split()
            return " ".join([ws[0] + "e1"] + ws[1:])
        self.assertEqual(phonemise_repeats("tim tim tim dAt kAm", g2p),
                         "tim tim time1 dAt kAm")
        self.assertEqual(phonemise_repeats("salAm donyA", g2p), "salAme1 donyA")

    def test_user_pronunciations_file(self):
        import tempfile
        from unittest import mock
        from hamzaban import pronounce

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "pronounce.txt"
            f.write_text("# comment\nمرد = مَرد\nKubernetes = کوبرنتیز\n", encoding="utf-8")
            with mock.patch.object(pronounce, "PRONOUNCE_FILE", f):
                pronounce._cache["rx"] = None
                self.assertEqual(pronounce.apply("مرد و مردم با Kubernetes"), "مَرد و مردم با کوبرنتیز")
            pronounce._cache["rx"] = None


class LiveModeTests(unittest.TestCase):
    """Videos the engine cannot fetch: the extension sends their sound as they play."""

    def test_live_source_keeps_continuous_audio_by_video_time(self):
        import numpy as np
        from hamzaban.media import LiveSource

        src = LiveSource()
        for k in range(5):  # 5 s from t=10, then a jump (seek) to t=60
            src.feed(10 + k, np.full(16000, k / 10, dtype=np.float32))
        src.feed(60, np.ones(16000, dtype=np.float32))
        self.assertEqual([tuple(round(x, 2) for x in s) for s in src.spans()], [(10, 15), (60, 61)])
        a = src.read(11.5, 2.0)
        self.assertEqual(len(a), 32000)
        self.assertAlmostEqual(float(a[0]), 0.1, places=5)
        self.assertEqual(len(src.read(14.0, 10.0)), 16000)  # stops at the hole
        src.feed(11, np.zeros(16000, dtype=np.float32))      # heard again after a seek back: replaced
        self.assertAlmostEqual(float(src.read(11.2, 0.1)[0]), 0.0)
        src.feed(100, np.ones(8000, dtype=np.float32), rate=2.0)  # at 2x, half a second covers 1 s of video
        self.assertEqual(src.spans()[-1], (100.0, 101.0))

    def test_unreadable_link_becomes_live_and_sentences_follow_the_audio(self):
        import tempfile
        import threading
        import time
        import numpy as np
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob
        from hamzaban.dubcache import AsrCache, TtsCache
        from hamzaban.media import MediaError

        heard = []

        def transcribe(audio, lang, prompt=None):
            heard.append(len(audio) / 16000)
            return [{"start": 0.5, "end": 3.0, "text": "First one."},
                    {"start": 3.5, "end": len(audio) / 16000 - 0.2, "text": "Cut off"}], "en"

        with tempfile.TemporaryDirectory() as d:
            engine = SimpleNamespace(cache_dir=Path(d), asr=SimpleNamespace(transcribe=transcribe),
                                     tts=None, cfg={"default_voice": "male_news"},
                                     asr_cache=AsrCache(Path(d) / "a", "w"), tts_cache=TtsCache(Path(d) / "t"))
            job = DubJob(engine, "https://example.com/frame", None, "omni_female", 30.0, "m",
                         speak=False, can_live=True)

            def fail():
                raise MediaError("no link")

            job.source.resolve = fail
            job._mt_loop = lambda: None
            job._boot()
            self.assertTrue(job.live and job.poll()["live"])
            for k in range(7):  # 7 s of sound from the playhead on
                job.source.feed(30 + k, np.full(16000, 0.2, dtype=np.float32))
            deadline = time.time() + 5
            while time.time() < deadline and not job.regions:
                time.sleep(0.05)
            job.stop()
            self.assertTrue(heard and heard[0] >= 3.0)  # LIVE_MIN
            self.assertEqual([job.sents[i].src for i in job.regions[0].sids], ["First one."])  # the cut one waits
            self.assertAlmostEqual(job.regions[0].a, 30.0)
            self.assertAlmostEqual(job.regions[0].b, 33.5)  # heard again from where the cut one starts

    def test_live_subtitles_come_with_the_translation_not_the_voice(self):
        import tempfile
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob, Region, Sentence

        with tempfile.TemporaryDirectory() as d:
            job = DubJob(SimpleNamespace(cache_dir=Path(d)), "https://example.com/x", None, "v", 0, "m", can_live=True)
            job.live = True
            region = Region(0, 5)
            for i, (a, b) in enumerate([(0.5, 2.0), (2.5, 4.5)], 1):
                s = Sentence(i, a, b, "x", region)
                job.sents[i] = s
                region.sids.append(i)
            job.regions = [region]
            job.sents[1].fa, job.sents[1].state = "یک", "tts"   # translated, not yet spoken
            job.tseq = job.sents[1].tseq = 1
            r = job.poll(5.0, 0, 0)
            self.assertEqual([x["fa"] for x in r["texts"]], ["یک"])
            self.assertEqual(r["segments"], [])                   # no voice yet
            self.assertEqual(job.poll(5.0, 0, 1)["texts"], [])     # already sent
            self.assertNotIn("texts", job.poll(5.0, 0))            # old extensions: unchanged reply

    def test_without_live_support_an_unreadable_link_is_still_an_error(self):
        import tempfile
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob
        from hamzaban.media import MediaError

        with tempfile.TemporaryDirectory() as d:
            job = DubJob(SimpleNamespace(cache_dir=Path(d)), "https://example.com/x", None, "v", 0, "m")

            def fail():
                raise MediaError("no link")

            job.source.resolve = fail
            job._boot()
            self.assertEqual(job.status, "error")


class HallucinationTests(unittest.TestCase):
    """Music and noise: Whisper's invented words are dropped, real short sentences kept."""

    def test_low_confidence_sentences_are_dropped(self):
        from hamzaban.asr import believable, words_to_sentences

        def words(text, p):
            out, t = [], 0.0
            for w in text.split():
                out.append({"start": t, "end": t + 0.3, "word": " " + w, "p": p})
                t += 0.35
            return out

        self.assertFalse(believable(words_to_sentences(words("you", 0.2))[0]))
        self.assertFalse(believable(words_to_sentences(words("So", 0.03))[0]))
        self.assertTrue(believable(words_to_sentences(words("So", 0.71))[0]))           # said for real
        self.assertTrue(believable(words_to_sentences(words("issue", 0.54))[0]))
        self.assertFalse(believable(words_to_sentences(words("the cat eats pizza on the bus.", 0.3))[0]))
        self.assertTrue(believable(words_to_sentences(words("the cat eats pizza on the bus.", 0.8))[0]))
        self.assertTrue(believable({"start": 0, "end": 1, "text": "no word timings"}))

    def test_live_window_of_music_is_skipped_not_reheard(self):
        import tempfile
        import time
        import numpy as np
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob
        from hamzaban.media import LiveSource

        calls = []

        def transcribe(audio, lang, prompt=None):
            calls.append(len(audio) / 16000)
            return [], "en"  # music: nothing believable

        with tempfile.TemporaryDirectory() as d:
            engine = SimpleNamespace(cache_dir=Path(d), asr=SimpleNamespace(transcribe=transcribe))
            job = DubJob(engine, "https://example.com/x", None, "v", 10.0, "m", speak=False, can_live=True)
            job.live, job.source = True, LiveSource()
            for k in range(6):
                job.source.feed(10 + k, np.full(16000, 0.1, dtype=np.float32))
            import threading
            th = threading.Thread(target=job._live_asr_loop, daemon=True)
            th.start()
            deadline = time.time() + 5
            while time.time() < deadline and not job.regions:
                time.sleep(0.05)
            job.stop()
            th.join(timeout=3)
            self.assertEqual(len(calls), 1)                   # listened once
            self.assertGreaterEqual(job.regions[0].b, 15.0)   # and moved past the music
            self.assertEqual(job.sents, {})


class TidyLineTests(unittest.TestCase):
    def test_alternatives_glosses_and_added_quotes_are_removed(self):
        from hamzaban.translator import tidy_line

        self.assertEqual(tidy_line("Mayday!", "کمک! (یا: درخواست کمک!)"), "کمک!")
        self.assertEqual(tidy_line("Mayday, Mayday!", '"کمک! کمک!" (یا "در خطر! در خطر!")'), "کمک! کمک!")
        self.assertEqual(tidy_line("Mayday, Mayday!", "کمک! کمک! (Mayday)"), "کمک! کمک!")
        self.assertEqual(tidy_line('He said "go".', "او گفت «برو»."), "او گفت «برو».")       # real quotes stay
        self.assertEqual(tidy_line("Use PHP (version 8).", "از PHP (نسخه‌ی ۸) استفاده کنید."),
                         "از PHP (نسخه‌ی ۸) استفاده کنید.")                                # real brackets stay


class IdiomTests(unittest.TestCase):
    def test_whole_sentence_idioms_get_the_persian_one(self):
        from hamzaban import idioms

        self.assertEqual(idioms.whole("Copy that."), "دریافت شد")
        self.assertEqual(idioms.whole("Big deal!"), "مهم نیست")
        self.assertEqual(idioms.whole("Roger that, over."), "دریافت شد")
        self.assertIsNone(idioms.whole("Please copy the file."))

    def test_inside_a_sentence_only_safe_idioms_become_their_meaning(self):
        from hamzaban import idioms

        self.assertEqual(idioms.plain("Don't spill the beans about it."), "Don't reveal the secret about it.")
        self.assertEqual(idioms.plain("Roger that, Major."), "understood, Major.")
        for untouched in ("Roger said he would come.", "Copy the file.", "Whatever you want.", "Time flies by."):
            self.assertEqual(idioms.plain(untouched), untouched)

    def test_the_users_own_idioms(self):
        import tempfile
        from unittest import mock
        from hamzaban import idioms

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "idioms.txt"
            f.write_text("# mine\nhit me up = contact me = بهم پیام بده\n", encoding="utf-8")
            with mock.patch.object(idioms, "IDIOMS_FILE", f):
                idioms._cache["rules"] = None
                self.assertEqual(idioms.whole("Hit me up!"), "بهم پیام بده")
                self.assertEqual(idioms.plain("Just hit me up later."), "Just contact me later.")
            idioms._cache["rules"] = None

    def test_model_alternatives_and_explanations_are_cut(self):
        from hamzaban.translator import tidy_line

        self.assertEqual(tidy_line("Once in a blue moon.", "به ندرت.\nیا:\nبه احتمال بسیار کم."), "به ندرت.")
        self.assertEqual(tidy_line("You nailed it!", "شما عالی بودید! / کارتان عالی بود!"), "شما عالی بودید!")
        self.assertEqual(tidy_line("x", "یک کار. (این یک عبارت اصطلاحی است به معنای هرگز)"), "یک کار.")
        self.assertEqual(tidy_line("Use 1/2 cup.", "نصف / یک دوم پیمانه"), "نصف / یک دوم پیمانه")


class LexiconApiTests(unittest.TestCase):
    """The ✎ panel and the popup add, replace and delete the user's words."""

    def test_add_replace_delete(self):
        import tempfile
        from unittest import mock
        from fastapi.testclient import TestClient
        from hamzaban import idioms, pronounce, server

        with tempfile.TemporaryDirectory() as d:
            files = {"pronounce": (Path(d) / "p.txt", 2), "idioms": (Path(d) / "i.txt", 3)}
            with mock.patch.dict(server.LEXICONS, files), \
                    mock.patch.object(pronounce, "PRONOUNCE_FILE", files["pronounce"][0]), \
                    mock.patch.object(idioms, "IDIOMS_FILE", files["idioms"][0]):
                c = TestClient(server.app, base_url="http://127.0.0.1")
                r = c.post("/api/lexicon/pronounce", json={"parts": ["Kubernetes", "کوبرنتیز"]})
                self.assertEqual(r.json()["entries"], [["Kubernetes", "کوبرنتیز"]])
                c.post("/api/lexicon/pronounce", json={"parts": ["kubernetes", "کوبِرنتیز"]})  # same word: replaced
                self.assertEqual(c.get("/api/lexicon/pronounce").json()["entries"], [["kubernetes", "کوبِرنتیز"]])
                pronounce._cache["rx"] = None
                self.assertEqual(pronounce.apply("با Kubernetes"), "با کوبِرنتیز")
                c.post("/api/lexicon/idioms", json={"parts": ["Hit me up!", "", "بهم پیام بده"]})
                idioms._cache["rules"] = None
                self.assertEqual(idioms.whole("Hit me up."), "بهم پیام بده")
                self.assertEqual(idioms.plain("Hit me up later."), "Hit me up later.")  # no meaning: whole only
                self.assertEqual(c.post("/api/lexicon/pronounce", json={"parts": ["a=b", "x"]}).status_code, 400)
                c.post("/api/lexicon/pronounce/delete", json={"key": "KUBERNETES"})
                self.assertEqual(c.get("/api/lexicon/pronounce").json()["entries"], [])
            pronounce._cache["rx"] = None
            idioms._cache["rules"] = None


class PronounceSuffixTests(unittest.TestCase):
    def test_rules_cover_suffixed_forms_and_identical_ones_are_refused(self):
        import tempfile
        from unittest import mock
        from fastapi.testclient import TestClient
        from hamzaban import pronounce, server

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.txt"
            with mock.patch.dict(server.LEXICONS, {"pronounce": (f, 2)}), mock.patch.object(pronounce, "PRONOUNCE_FILE", f):
                c = TestClient(server.app, base_url="http://127.0.0.1")
                bad = c.post("/api/lexicon/pronounce", json={"parts": ["پتانسیل‌هایی", "پتانسیل‌هایی"]})
                self.assertEqual(bad.status_code, 400)
                c.post("/api/lexicon/pronounce", json={"parts": ["پتانسیل‌هایی", "پُتانسیل"]})
                self.assertEqual(c.get("/api/lexicon/pronounce").json()["entries"], [["پتانسیل", "پُتانسیل"]])
                pronounce._cache["rx"] = None
                self.assertEqual(pronounce.apply("پتانسیل‌های زیاد و پتانسیلی"), "پُتانسیل‌های زیاد و پُتانسیلی")
                self.assertEqual(pronounce.apply("پتانسیلگرا"), "پتانسیلگرا")  # another word: untouched
                self.assertEqual(c.get("/api/spoken", params={"text": "پتانسیل ۳"}).json()["spoken"], "پُتانسیل سه")
            pronounce._cache["rx"] = None


class HomographTests(unittest.TestCase):
    """Two-reading words: the dub reads them as the English sentence says."""

    def test_reading_follows_the_english(self):
        from hamzaban.pronounce import in_context

        cases = [
            ("این اشکال هندسی زیبا هستند.", "These geometric shapes are beautiful.", "این شکل‌های هندسی زیبا هستند."),
            ("همه‌ی اشکال را ببین.", "Look at all the shapes.", "همه‌ی شکل‌ها را ببین."),
            ("اشکالی ندارد.", "No problem.", "اشکالی ندارد."),
            ("این کد یک اشکال دارد.", "This code has a shape problem.", "این کد یک اشکال دارد."),  # «یک» -> singular: no
            ("مرد مرد.", "The man died.", "مرد مُرد."),
            ("او را کشت.", "He killed him.", "او را کُشت."),
            ("کشتی ورزش ملی است.", "Wrestling is a national sport.", "کُشتی ورزش ملی است."),
            ("او در کشتی بود.", "He was on the ship.", "او در کَشتی بود."),
            ("او در کشتی بود.", "", "او در کشتی بود."),                                      # no English: as is
        ]
        for fa, en, want in cases[:3] + cases[4:]:
            self.assertEqual(in_context(fa, en), want, fa)

    def test_a_rule_for_a_two_reading_word_is_refused(self):
        from fastapi.testclient import TestClient
        from hamzaban import server

        c = TestClient(server.app, base_url="http://127.0.0.1")
        r = c.post("/api/lexicon/pronounce", json={"parts": ["اشکال", "اَشکال"]})
        self.assertEqual(r.status_code, 400)


class EshkalTests(unittest.TestCase):
    def test_eshkal_is_spoken_as_a_word_the_voice_cannot_misread(self):
        from hamzaban.pronounce import in_context
        from hamzaban.speech import for_voice

        say = lambda fa, en="": for_voice(in_context(fa, en))
        self.assertEqual(say("اشکالی ندارد.", "No problem."), "مشکلی ندارد.")
        self.assertEqual(say("این کد یک اشکال دارد."), "این کُد یک مشکل دارد.")
        self.assertEqual(say("اشکالات زیادی دارد."), "مشکلات زیادی دارد.")
        self.assertEqual(say("این اشکال هندسی زیبا هستند.", "These geometric shapes are beautiful."),
                         "این شکل‌های هندسی زیبا هستند.")


class SpokenPersianTests(unittest.TestCase):
    def test_written_becomes_spoken_without_touching_ambiguous_forms(self):
        from hamzaban.spoken import colloquial

        cases = {
            "من می‌خواهم آن را ببینم.": "من می‌خوام اونو ببینم.",
            "آیا می‌توانید به من بگویید چه اتفاقی افتاده است؟": "می‌تونید به من بگید چه اتفاقی افتاده؟",
            "او همیشه می‌گوید که این کار بسیار خوب است.": "اون همیشه می‌گه که این کار خیلی خوبه.",
            "این همان چیزی است که می‌خواستم.": "این همون چیزیه که می‌خواستم.",
            "نمی‌دانم چرا این‌طور می‌شود.": "نمی‌دونم چرا این‌طور می‌شه.",
            "او خسته است.": "اون خسته‌ست.",
            "هر روز کار می‌کرد و نان می‌خورد.": "هر روز کار می‌کرد و نان می‌خورد.",   # past: unchanged
            "بدهی ملی بسیار بالا است.": "بدهی ملی خیلی بالاست.",                       # «بدهی» = debt
        }
        for written, said in cases.items():
            self.assertEqual(colloquial(written), said, written)


class SavedFixTests(unittest.TestCase):
    def test_a_saved_fix_goes_into_the_code_and_redoes_unheard_sentences(self):
        import tempfile
        from types import SimpleNamespace
        from unittest import mock
        from fastapi.testclient import TestClient
        from hamzaban import pronounce, server
        from hamzaban.dubbing import DubJob, Region, Sentence

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            user, code = d / "pronounce.txt", d / "code" / "pronounce.txt"
            job = DubJob(SimpleNamespace(cache_dir=d), "https://example.com/v", None, "omni_female", 0, "m")
            job.lang = "en"
            region = Region(0, 20)
            for i, (a, b, fa) in enumerate([(1, 3, "پتانسیل بالا"), (10, 12, "پتانسیل زیاد"), (14, 15, "سلام")], 1):
                s = Sentence(i, a, b, "x", region)
                s.fa, s.state = fa, "ready"
                s.said, s.asked = job._said(s), "x"
                job.sents[i] = s
            job.playhead = 5.0                              # the first one was heard already
            with mock.patch.dict(server.LEXICONS, {"pronounce": (user, 2)}), \
                    mock.patch.object(pronounce, "PRONOUNCE_FILE", user), \
                    mock.patch.object(server, "_code_copies", lambda kind: [code]), \
                    mock.patch.dict(server.engine.jobs, {job.id: job}, clear=True):
                pronounce._cache["rx"] = None
                TestClient(server.app, base_url="http://127.0.0.1").post(
                    "/api/lexicon/pronounce", json={"parts": ["پتانسیل", "پُتانسیل"]})
                self.assertIn("پتانسیل = پُتانسیل", code.read_text(encoding="utf-8"))  # kept in the code
                self.assertEqual([job.sents[i].state for i in (1, 2, 3)], ["ready", "tts", "ready"])
                self.assertEqual(job.sents[2].rev, 1)
            pronounce._cache["rx"] = None


class DateTests(unittest.TestCase):
    def test_dates_years_decades_centuries(self):
        from hamzaban.numbers import speak_numbers

        cases = {
            "در ۱۵ مارس ۲۰۲۳": "در پانزدهم مارس دو هزار و بیست و سه",
            "در 15 March 2023": "در پانزدهم مارس دو هزار و بیست و سه",
            "تاریخ ۱۴۰۲/۰۵/۱۲": "تاریخ دوازدهم مرداد هزار و چهارصد و دو",
            "در 2023-03-15": "در پانزدهم مارس دو هزار و بیست و سه",
            "قرن ۲۱": "قرن بیست و یکم",
            "قَرن ۱۹": "قَرن نوزدهم",
            "دهه‌ی ۱۹۹۰s": "دهه‌ی هزار و نهصد و نود",
            "در 1990s": "در دهه‌ی هزار و نهصد و نود",
            "the 90s": "دهه‌ی نود",
            "۲۰ فروردین ۱۴۰۳": "بیستم فروردین هزار و چهارصد و سه",
            "۳ مهر": "سوم مهر",
            "در سال ۲۰۲۴": "در سال دو هزار و بیست و چهار",
            "از ۱۹۳۹ تا ۱۹۴۵": "از هزار و نهصد و سی و نه تا هزار و نهصد و چهل و پنج",
            "ساعت ۱۰:۳۰": "ساعت ده و سی دقیقه",
        }
        for written, said in cases.items():
            self.assertEqual(speak_numbers(written), said, written)


class PhonemeFixTests(unittest.TestCase):
    """ParSiGo voices: a fix written in phonemes replaces exactly that word's phonemes."""

    def test_phoneme_fix_in_place_with_ezafe(self):
        import tempfile
        from unittest import mock
        from hamzaban import pronounce

        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "p.txt"
            f.write_text("نیمکره = nimkore\nکد = کُد\n", encoding="utf-8")
            with mock.patch.object(pronounce, "PRONOUNCE_FILE", f):
                pronounce._cache["rx"] = None
                fake = {"نیمکره": "nimkare"}.get
                self.assertEqual(pronounce.fix_phonemes("نیمکره‌ی شمالی", "nimkareye1 SomAli", fake),
                                 "nimkoreye1 SomAli")
                self.assertEqual(pronounce.fix_phonemes("دو نیمکره.", "do nimkare", fake), "do nimkore")
                # counts differ (a number): found by the word's own phonemes
                self.assertEqual(pronounce.fix_phonemes("۲ نیمکره", "do nimkare", fake), "do nimkore")
                self.assertNotIn("nimkore", pronounce.apply("نیمکره شمالی"))         # not put into the text
                self.assertEqual(pronounce.apply("کد"), "کُد")                       # this one is
                self.assertIn("nimkore", pronounce.signature())
            pronounce._cache["rx"] = None


class LiveRobustnessTests(unittest.TestCase):
    def test_a_far_seek_back_keeps_the_new_sound(self):
        import numpy as np
        from hamzaban.media import LiveSource

        src = LiveSource()
        src.feed(2000, np.ones(16000, dtype=np.float32))
        src.feed(100, np.ones(16000, dtype=np.float32))      # 30 minutes back
        self.assertEqual([round(a) for a, _ in src.spans()], [100])  # the new sound stays, the far one goes

    def test_one_failed_translation_does_not_end_the_dub(self):
        import tempfile
        import threading
        import time
        from types import SimpleNamespace
        from hamzaban.dubbing import DubJob, Region, Sentence
        from hamzaban.translator import TranslationError

        def translate_lines(lines, model, lang):
            if len(lines) > 1 or "bad" in lines[0]:
                raise TranslationError("خطای Ollama: prediction aborted, token repeat limit reached")
            return ["خوب"]

        with tempfile.TemporaryDirectory() as d:
            engine = SimpleNamespace(cache_dir=Path(d), translator=SimpleNamespace(translate_lines=translate_lines))
            job = DubJob(engine, "https://example.com/x", None, "v", 0, "m", speak=False)
            region = Region(0, 10)
            for i, src in enumerate(["good one", "bad one"], 1):
                job.sents[i] = Sentence(i, i, i + 1, src, region)
                region.sids.append(i)
            job.regions = [region]
            th = threading.Thread(target=job._mt_loop, daemon=True)
            th.start()
            deadline = time.time() + 3
            while time.time() < deadline and any(s.state != "ready" for s in job.sents.values()):
                time.sleep(0.02)
            job.stop()
            self.assertNotEqual(job.status, "error")
            self.assertEqual([job.sents[1].fa, job.sents[2].fa], ["خوب", ""])
