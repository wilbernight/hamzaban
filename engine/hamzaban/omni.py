"""OmniVoice voices (k2-fsa OmniVoice via mlx-audio, see ../omni_worker.py).

The engine VoiceStudio is built on, run directly on the Apple GPU: clear
Persian, one steady voice per reference clip, several sentences per batch,
and each sentence generated at the length its dubbing slot allows.

Voices: "omni_female" / "omni_male" (reference clips in omnivoice-voices/),
and any clip + transcript (the dub's "sounds like the original speaker").
"""
import sys

from . import config
from .aava import join_sentences, split_sentences
from .config import APP_HOME
from .workers import VoiceWorker

SAMPLE_RATE = 24000
_cfg = config.load()
MODEL = APP_HOME / "omnivoice-bf16"
VOICES_DIR = APP_HOME / "omnivoice-voices"
VOICES = {"omni_female": "female", "omni_male": "male"}

worker = VoiceWorker("omni", 17862, APP_HOME / "venv-omni" / "bin" / "python",
                     APP_HOME / "engine" / "omni_worker.py",
                     required=[MODEL / "config.json", VOICES_DIR / "voices.json"],
                     env={"HAMZABAN_OMNI_DIR": str(MODEL), "HAMZABAN_OMNI_VOICES": str(VOICES_DIR),
                          "HAMZABAN_OMNI_PORT": "17862",
                          # speed/memory trade-offs, measured in HANDOFF §6
                          "HAMZABAN_OMNI_STEPS": str(_cfg["omni_steps"]),
                          "HAMZABAN_OMNI_QBITS": str(_cfg["omni_qbits"]),
                          "HAMZABAN_OMNI_CODEC_DTYPE": str(_cfg["omni_codec_dtype"]),
                          "HAMZABAN_OMNI_CACHE_MB": str(_cfg["mlx_cache_mb"])})


def available() -> bool:
    return worker.available()


def is_omni(voice: str) -> bool:
    return voice in VOICES


def synth_many(texts, voice: str = "omni_female", slots=None, ref=None):
    """Sentences in one batch -> float32 arrays (24 kHz).
    slots: seconds available per sentence (None = natural length).
    ref: (wav_path, transcript) to speak in that voice instead."""
    payload = {"texts": list(texts), "voice": VOICES.get(voice, "female")}
    if slots:
        payload["slots"] = [None if s is None else float(s) for s in slots]
    if ref:
        payload["ref_wav"], payload["ref_text"] = str(ref[0]), ref[1]
    return worker.synth(payload)


def synth(text: str, voice: str = "omni_female"):
    """A paragraph: all its sentences in one batch, joined with short pauses."""
    sentences = split_sentences(text)
    return join_sentences(synth_many(sentences, voice) if sentences else [])


if __name__ == "__main__":  # manual test: python -m hamzaban.omni "متن" omni_male
    import soundfile as sf

    sf.write("omni_test.wav", synth(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "omni_female"), SAMPLE_RATE)
