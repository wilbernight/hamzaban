"""Aava voices (Orpheus-3B Persian, see ../voice_worker.py and ../aava_mlx.py).

Voices: "aava_male" / "aava_female" hold one steady voice; plain "aava"
(the first version) means the male voice. Sentences are sent in batches,
which is what makes Aava fast enough for dubbing.
"""
import re
import sys

from .config import APP_HOME
from .workers import VoiceWorker

SAMPLE_RATE = 24000
MODEL = APP_HOME / "aava-mlx-4"
VOICES = {"aava_female": "female", "aava_male": "male", "aava": "male"}

worker = VoiceWorker("aava", 17861, APP_HOME / "venv-aava" / "bin" / "python",
                     APP_HOME / "engine" / "voice_worker.py", required=[MODEL / "config.json"],
                     env={"HAMZABAN_AAVA_DIR": str(MODEL), "HAMZABAN_VOICE_PORT": "17861"})

SENTENCES = re.compile(r"(?<=[.!?؟؛])\s+|\n+")


def available() -> bool:
    return worker.available()


def is_aava(voice: str) -> bool:
    return voice in VOICES


def synth_many(texts, voice: str):
    """Several sentences in one batch -> list of float32 arrays (24 kHz)."""
    return worker.synth({"texts": list(texts), "voice": VOICES.get(voice, "male")})


def join_sentences(wavs, gap_s: float = 0.3):
    import numpy as np

    if not wavs:
        return np.zeros(0, dtype=np.float32)
    gap = np.zeros(int(gap_s * SAMPLE_RATE), dtype=np.float32)
    parts = []
    for w in wavs:
        if parts:
            parts.append(gap)
        parts.append(w)
    return np.concatenate(parts)


def split_sentences(text: str):
    return [s.strip() for s in SENTENCES.split(text) if re.search(r"\w", s)]


def synth(text: str, voice: str = "aava_male"):
    """A paragraph: all its sentences in one batch, joined with short pauses."""
    sentences = split_sentences(text)
    return join_sentences(synth_many(sentences, voice) if sentences else [])


if __name__ == "__main__":  # manual test: python -m hamzaban.aava "متن" aava_female
    import soundfile as sf

    sf.write("aava_test.wav", synth(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "aava_male"), SAMPLE_RATE)
