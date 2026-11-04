"""Work a dub already did, kept per video so it is never done twice.

AsrCache   Whisper's timed sentences per video (yt-dlp's extractor:id), one
           JSON file per video and Whisper model. Switching the voice, opening
           the video again or seeking back reads them instead of listening again.
TtsCache   finished dub clips (WAV) per voice, Persian text and slot length,
           hard-linked into each job's folder (no extra disk for the job).

Both are plain files under cache/, so they survive engine restarts; old
entries are pruned by the engine's reaper.
"""
import hashlib
import json
import os
import shutil
import threading
import time
import wave
from pathlib import Path


def _digest(*parts) -> str:
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode()).hexdigest()[:32]


class AsrCache:
    MAX_AGE = 60 * 86400  # forget videos not opened for two months

    def __init__(self, root: Path, model: str):
        self.root = Path(root)
        self.model = model
        self.lock = threading.Lock()

    def _path(self, key: str) -> Path:
        return self.root / f"{_digest('asr1', self.model, key)}.json"

    def load(self, key: str | None) -> list[dict]:
        """[{a, b, lang, sents: [{start, end, text}]}] in absolute seconds."""
        if not key:
            return []
        p = self._path(key)
        try:
            with self.lock:
                data = json.loads(p.read_text(encoding="utf-8"))
            os.utime(p)
            return data.get("regions", [])
        except (OSError, ValueError):
            return []

    def add(self, key: str | None, region: dict, known: list[dict]):
        """Remember one transcribed region; known is the caller's in-memory list."""
        if not key:
            return
        known.append(region)
        p = self._path(key)
        with self.lock:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps({"key": key, "regions": known}, ensure_ascii=False), encoding="utf-8")
                tmp.replace(p)
            except OSError:
                pass

    def prune(self):
        cutoff = time.time() - self.MAX_AGE
        for p in self.root.glob("*.json") if self.root.exists() else []:
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass


class TtsCache:
    MAX_BYTES = 1_500_000_000  # ~4 hours of dub audio

    def __init__(self, root: Path):
        self.root = Path(root)

    @staticmethod
    def key(voice: str, text: str, seconds: float) -> str:
        return _digest("tts2", voice, text, round(seconds, 2))

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.wav"

    def fetch(self, key: str, target: Path) -> float | None:
        """Put the cached clip at target; -> its length in seconds, or None."""
        src = self._path(key)
        try:
            with wave.open(str(src)) as w:
                dur = w.getnframes() / w.getframerate()
            target.unlink(missing_ok=True)
            try:
                os.link(src, target)
            except OSError:
                shutil.copyfile(src, target)
            os.utime(src)
            return dur
        except (OSError, EOFError, wave.Error):
            return None

    def store(self, key: str, path: Path):
        dst = self._path(key)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.unlink(missing_ok=True)
            os.link(path, dst)
        except OSError:
            pass

    def prune(self):
        """Drop the least recently used clips beyond MAX_BYTES."""
        if not self.root.exists():
            return
        files = []
        for p in self.root.glob("*/*.wav"):
            try:
                st = p.stat()
                files.append((st.st_mtime, st.st_size, p))
            except OSError:
                pass
        total = sum(f[1] for f in files)
        for _, size, p in sorted(files):
            if total <= self.MAX_BYTES:
                break
            try:
                p.unlink()
                total -= size
            except OSError:
                pass
