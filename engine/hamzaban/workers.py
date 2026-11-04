"""Separate voice-model processes (Aava, OmniVoice) and how the engine talks to them.

Each worker lives in its own Python environment (their ML libraries clash
with the engine's), listens on 127.0.0.1, is started on first use and exits by
itself when idle. Workers are children of the engine, so switching the engine
off from the menu-bar app stops them too.
"""
import base64
import io
import json
import os
import subprocess
import threading
import time
import urllib.request
import wave
from pathlib import Path

from .config import APP_HOME


class VoiceWorker:
    def __init__(self, name: str, port: int, python: Path, script: Path, required=(), env=None):
        self.name = name
        self.url = f"http://127.0.0.1:{port}"
        self.python, self.script = Path(python), Path(script)
        self.required = [Path(p) for p in required]
        self.env = dict(env or {})
        self._lock = threading.Lock()
        self._proc = None
        self._log = None
        self._busy = 0

    def available(self) -> bool:
        return all(p.exists() for p in [self.python, self.script, *self.required])

    def _alive(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=1) as r:
                return r.status == 200
        except OSError:
            return False

    def ensure(self):
        if self._alive():
            return
        with self._lock:
            if self._alive():
                return
            if self._proc is not None:
                self._proc.poll()            # reap a worker that exited while idle
            if self._log is None:
                self._log = open(APP_HOME / "logs" / f"{self.name}.log", "ab")
            self._proc = subprocess.Popen([str(self.python), str(self.script)],
                                          env={**os.environ, **self.env}, stdout=self._log,
                                          stderr=self._log, stdin=subprocess.DEVNULL)
            for _ in range(120):
                if self._alive():
                    return
                if self._proc.poll() is not None:
                    break
                time.sleep(0.5)
            raise RuntimeError(f"صدای {self.name} راه نیفتاد؛ لاگ: logs/{self.name}.log")

    def stop_if_idle(self) -> bool:
        """Quit the worker now (it frees its model's memory) unless it is speaking.
        Returns False while a request is still running, so the caller can retry."""
        with self._lock:
            if self._busy:
                return False
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                self._proc = None
            return True

    def synth(self, payload: dict, timeout: float = 600):
        """POST /synth -> list of float32 arrays (24 kHz mono)."""
        import numpy as np

        with self._lock:
            self._busy += 1
        try:
            self.ensure()
            req = urllib.request.Request(self.url + "/synth", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                wavs = json.loads(r.read())["wavs"]
        finally:
            with self._lock:
                self._busy -= 1
        out = []
        for b64 in wavs:
            with wave.open(io.BytesIO(base64.b64decode(b64))) as w:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            out.append(pcm.astype(np.float32) / 32767)
        return out
