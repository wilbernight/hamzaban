"""Install locations and user settings for the Hamzaban engine."""
import json
import os
from pathlib import Path

VERSION = "1.0.0"

APP_HOME = Path(os.environ.get(
    "HAMZABAN_HOME", Path.home() / "Library" / "Application Support" / "Hamzaban"))
CONFIG_FILE = APP_HOME / "config.json"
CACHE_DIR = APP_HOME / "cache"
PARSIGO_DIR = APP_HOME / "parsigo"      # clone of github.com/nimaone/persian_tts
USER_VOICES_DIR = APP_HOME / "voices"   # extra reference voices (<= 5 s WAV)
# 453K-word list from github.com/ali2000hos/persian-writing (ZWNJ compounds)
PERSIAN_WORDS = Path(os.environ.get(
    "HAMZABAN_PERSIAN_WORDS", APP_HOME / "persian-writing" / "assets" / "persian_words.txt"))

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 17860,
    "ollama_url": "http://127.0.0.1:11434",
    # TranslateGemma (Google, 2026): translation-tuned Gemma 3, 55 languages
    "email_model": "translategemma:12b",
    # English e-mails: TranslateGemma 12B fine-tuned (LoRA) on English programming lectures:
    # more natural Persian, technical terms as «فارسی (english)». Not installed -> email_model.
    "email_model_en": "translategemma-fa:12b",
    "dub_model": "translategemma:4b",
    "whisper_model": "mlx-community/whisper-large-v3-turbo-q4",  # 4-bit: same speed and words, 1.1 GB less
    "default_voice": "male_news",
    "keep_alive": "15m",
    # MLX keeps freed GPU buffers as a cache, which grew to ~10 GB in the voice worker
    # and several GB next to Whisper: cap it (no measurable slowdown)
    "mlx_cache_mb": 256,
    # the dub voice talks spoken Persian («می‌خوام», «خوبه»); "written" reads the translation as is
    "dub_style": "spoken",
    # OmniVoice: diffusion steps (16 = cleanest, 8 = ~1.7x faster), backbone quantized
    # at load (0 = bf16, 8 = half the memory), codec precision ("" = float32).
    # Measured on the M5 (16 dub sentences, Whisper character error): bf16/16 steps
    # RTF 0.18, 0.024 · 8-bit/16 steps RTF 0.21, 0.019 (clearest; 1.6 GB less at peak)
    # · 8-bit/8 steps RTF 0.11, 0.029. Clarity first: words were being swallowed.
    "omni_steps": 16,
    "omni_qbits": 8,
    "omni_codec_dtype": "bfloat16",
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg
