"""Persian speech with ParSiGo (github.com/nimaone/persian_tts).

ParSiGo runs pocket-tts-farsi-v2 fully in ONNX Runtime (CPU, no torch) and
has its own Persian G2P, so ezafe and ZWNJ words are read correctly. It
clones the voice of any <= 5 s reference WAV, which is how the built-in
voices (and the "sounds like the original speaker" option) work.
Model licence: CC-BY-NC-4.0 — personal, non-commercial use.
"""
import io
import sys
import threading
import time
import wave
from pathlib import Path

from . import aava, omni
from .speech import for_voice

SAMPLE_RATE = 24000  # ParSiGo, OmniVoice and Aava all speak at 24 kHz
IDLE_UNLOAD = 600.0  # free ParSiGo's ~0.5 GB after 10 minutes without a ParSiGo voice

VOICE_NAMES = {
    "male_news": "آقا · خبری",
    "female_narration": "بانو · روایت",
    "male_hello": "آقا · صمیمی",
}


class PersianTTS:
    def __init__(self, parsigo_dir: Path, user_voices: Path):
        self.dir = Path(parsigo_dir)
        self.user_voices = Path(user_voices)
        self.lock = threading.Lock()
        self._eng = None
        self._last_used = 0.0

    @property
    def eng(self):
        if self._eng is None:
            scripts = str(self.dir / "scripts")
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            from tts_onnx import OnnxTts

            eng = OnnxTts(pkg_dir=self.dir / "model" / "onnx")
            self._hook_g2p(eng)
            self._eng = eng
        return self._eng

    @staticmethod
    def _hook_g2p(eng):
        """The user's phoneme fixes go into ParSiGo's phonemes (pronounce.fix_phonemes)."""
        from .pronounce import fix_phonemes, phonemise_repeats

        if not hasattr(eng, "_g2p"):
            from g2p_onnx import OnnxG2P

            eng._g2p = OnnxG2P(eng.dir)
        own = eng._g2p.phonemise

        def g2p(text, keep_ezafe):
            return fix_phonemes(text, own(text, keep_ezafe=keep_ezafe), lambda w: own(w, keep_ezafe=False))

        def phonemise(text, keep_ezafe=False):
            return phonemise_repeats(text, lambda t: g2p(t, keep_ezafe))

        eng._g2p.phonemise = phonemise

    def phonemes(self, text: str) -> str:
        """What a ParSiGo voice says for text (with the user's fixes), without speaking.
        Uses only the small G2P model when the voice itself is not loaded."""
        with self.lock:
            if self._eng is not None:
                return self._eng._g2p.phonemise(text, keep_ezafe=True)
            if getattr(self, "_g2p_only", None) is None:
                scripts = str(self.dir / "scripts")
                if scripts not in sys.path:
                    sys.path.insert(0, scripts)
                from types import SimpleNamespace

                holder = SimpleNamespace(dir=self.dir / "model" / "onnx")
                self._hook_g2p(holder)
                self._g2p_only = holder._g2p
            return self._g2p_only.phonemise(text, keep_ezafe=True)

    @property
    def sample_rate(self) -> int:
        return SAMPLE_RATE  # a constant: asking ParSiGo would load it for nothing

    def release_if_idle(self, idle: float = IDLE_UNLOAD) -> bool:
        """Unload ParSiGo when no ParSiGo voice was used for a while (a dub with
        OmniVoice never needs it). It loads again on the next use (~15 s)."""
        with self.lock:
            if self._eng is None or time.monotonic() - self._last_used < idle:
                return False
            self._eng = None
        import gc

        gc.collect()
        return True

    def voices(self):
        out = []
        for p in sorted((self.dir / "voices").glob("*.wav")):
            out.append({"id": p.stem, "name": VOICE_NAMES.get(p.stem, p.stem)})
        for p in sorted(self.user_voices.glob("*.wav")):
            out.append({"id": f"user:{p.stem}", "name": p.stem})
        if omni.available():  # OmniVoice: clearest, steady voice, ~6x faster than real time
            out.append({"id": "omni_female", "name": "VoiceStudio (اُمنی) · بانو — پیشنهادی"})
            out.append({"id": "omni_male", "name": "VoiceStudio (اُمنی) · آقا — پیشنهادی"})
        if aava.available():  # Orpheus-3B Persian: natural, but slower than real time and hot
            out.append({"id": "aava_female", "name": "آوا · بانو (کُند و پرمصرف؛ برای دوبله نه)"})
            out.append({"id": "aava_male", "name": "آوا · آقا (کُند و پرمصرف؛ برای دوبله نه)"})
        return out

    def voice_path(self, voice: str) -> Path:
        if voice.startswith("user:"):
            p = self.user_voices / f"{voice[5:]}.wav"
        elif voice.startswith("/"):
            p = Path(voice)                       # a clone prompt made by a dub job
        else:
            p = self.dir / "voices" / f"{voice}.wav"
        if not p.exists():
            p = self.dir / "voices" / "male_news.wav"
        return p

    def synth(self, text: str, voice: str, mode: str = "pack"):
        """Persian text -> float32 mono audio at self.sample_rate."""
        import numpy as np

        parsigo = not ((omni.is_omni(voice) and omni.available()) or (aava.is_aava(voice) and aava.available()))
        text = for_voice(text, phonetic=parsigo).strip()  # numbers as words, English in Persian letters
        if not text:
            return np.zeros(0, dtype=np.float32)
        if omni.is_omni(voice) and omni.available():
            return omni.synth(text, voice)  # worker processes, 24 kHz like ParSiGo
        if aava.is_aava(voice) and aava.available():
            return aava.synth(text, voice)
        with self.lock:
            self._last_used = time.monotonic()
            out = self.eng.synthesize_text(text, str(self.voice_path(voice)), mode=mode)
            self._last_used = time.monotonic()
            return out

    def batch_size(self, voice: str) -> int:
        """Aava generates several sentences together for little extra time. OmniVoice
        speaks one sentence per call (batches garble short sentences), so each one is
        ready, and playable, as soon as it is spoken."""
        return 6 if aava.is_aava(voice) and aava.available() else 1

    def synth_many(self, texts, voice: str, slots=None, ref=None):
        """Sentences -> audio. slots: seconds each may last (OmniVoice generates
        that length directly). ref: (wav, transcript) to speak in that voice."""
        import numpy as np

        if (omni.is_omni(voice) or ref) and omni.available():
            def run(spoken, sl):
                return omni.synth_many(spoken, voice if omni.is_omni(voice) else "omni_female",
                                       slots=sl, ref=ref)
        elif aava.is_aava(voice) and aava.available():
            def run(spoken, sl):
                return aava.synth_many(spoken, voice)
        else:  # ParSiGo, one sentence at a time (it clones from a wav path too)
            v = str(ref[0]) if ref else voice
            return [self.synth(t, v) for t in texts]
        spoken = [for_voice(t).strip() for t in texts]
        keep = [i for i, t in enumerate(spoken) if t]
        out = [np.zeros(0, dtype=np.float32) for _ in texts]
        if keep:
            wavs = run([spoken[i] for i in keep], [slots[i] for i in keep] if slots else None)
            for i, wav in zip(keep, wavs):
                out[i] = wav
        return out

    def fit(self, audio, max_seconds: float, max_speedup: float = 1.4):
        """Speed speech up (pitch kept) so it fits a dubbing slot."""
        import numpy as np

        dur = len(audio) / self.sample_rate
        if max_seconds <= 0 or dur <= max_seconds * 1.03:
            return audio
        factor = min(max_speedup, dur / max_seconds)
        from pedalboard import time_stretch

        a = np.ascontiguousarray(audio, dtype=np.float32)
        return time_stretch(a, self.sample_rate, stretch_factor=factor).reshape(-1).astype(np.float32)


def release_other_voices(voice: str, wait: float = 120.0):
    """Keep only the voice in use in memory: a second voice model next to Whisper
    and the translator makes a 16 GB Mac swap, which is slow and heats it up.
    Waits (in the caller's thread) for a worker that is still finishing a batch."""
    uses_omni = omni.is_omni(voice) or voice == "clone"
    idle = [w for w, keep in ((omni.worker, uses_omni), (aava.worker, aava.is_aava(voice))) if not keep]
    deadline = time.monotonic() + wait
    while idle and time.monotonic() < deadline:
        idle = [w for w in idle if not w.stop_if_idle()]
        if idle:
            time.sleep(2.0)


def to_wav_bytes(audio, sample_rate: int) -> bytes:
    import numpy as np

    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()
