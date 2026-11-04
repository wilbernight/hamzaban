"""Getting audio out of a video link: yt-dlp finds the stream, ffmpeg cuts it.

Any window of the audio can be read straight from the network (ffmpeg seeks
with HTTP range requests), so dubbing can start anywhere in a long video
without downloading it first. A full copy of the audio track is fetched in
the background; once it is on disk the job reads from it instead.
"""
import contextlib
import os
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


class MediaError(RuntimeError):
    pass


def clean_cookies(cookies) -> list[dict]:
    """Cookies the extension sent for a YouTube link: youtube.com only, plain values."""
    out = []
    for c in (cookies or [])[:300] if isinstance(cookies, list) else []:
        try:
            domain, name, value = str(c["domain"]), str(c["name"]), str(c["value"])
        except (KeyError, TypeError):
            continue
        if not re.fullmatch(r"\.?([\w-]+\.)*youtube\.com", domain) or any(ch in name + value for ch in "\t\r\n"):
            continue
        out.append({"domain": domain, "path": str(c.get("path") or "/"), "secure": bool(c.get("secure")),
                    "host_only": bool(c.get("hostOnly")), "expires": int(c.get("expires") or 0),
                    "name": name, "value": value})
    return out


@contextlib.contextmanager
def cookie_file(cookies, folder: Path):
    """A Netscape cookie file readable only by this user, deleted right after use."""
    if not cookies:
        yield None
        return
    path = Path(folder) / f".cookies-{uuid.uuid4().hex}.txt"
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c["domain"] if c["host_only"] or c["domain"].startswith(".") else "." + c["domain"]
        lines.append("\t".join([domain, "FALSE" if c["host_only"] else "TRUE", c["path"],
                                "TRUE" if c["secure"] else "FALSE", str(c["expires"]), c["name"], c["value"]]))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        yield str(path)
    finally:
        path.unlink(missing_ok=True)


def _ydl_opts(extra=None):
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "format": "bestaudio/best", "socket_timeout": 20, "noprogress": True,
            "ffmpeg_location": ffmpeg_exe()}
    opts.update(extra or {})
    return opts


def friendly_error(detail: str) -> str:
    low = detail.lower()
    if "drm" in low:
        return "این ویدیو قفل‌شده (DRM) است و صدایش قابل دریافت نیست."
    if any(k in low for k in ("login", "log in", "sign in", "authenticat", "cookies", "private", "members")):
        return "این ویدیو فقط برای کاربرِ واردشده پخش می‌شود و موتور به آن دسترسی ندارد."
    if "unsupported url" in low:
        return "از این سایت پشتیبانی نمی‌شود (yt-dlp لینک را نمی‌شناسد)."
    if "unavailable" in low or "not available" in low:
        return "این ویدیو در دسترس نیست."
    return "صدای این ویدیو پیدا نشد" + (f" ({detail[:160]})" if detail else "")


class AudioSource:
    def __init__(self, page_url: str, media_url: str | None, cache_dir: Path, cookies=None):
        self.page_url = page_url
        self.cookies = clean_cookies(cookies)  # signed-in YouTube session from the extension (memory only)
        self.media_url = media_url
        self.cache_dir = cache_dir
        self.title = ""
        self.key = None  # "Youtube:<id>": the same video from any link (playlist, time, mirror)
        self.duration = None
        self.stream_url = None
        self.headers = {}
        self.local_file: Path | None = None
        self._dl_thread = None
        self._remote_failed = False  # the site refused direct reads: use the downloaded copy

    def resolve(self):
        from yt_dlp import YoutubeDL

        err = None
        try:
            with cookie_file(self.cookies, self.cache_dir) as cf, YoutubeDL(_ydl_opts({"cookiefile": cf})) as ydl:
                info = ydl.extract_info(self.page_url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
            self.title = info.get("title") or ""
            if info.get("id"):
                self.key = f"{info.get('extractor_key') or info.get('extractor') or ''}:{info['id']}"
            self.duration = info.get("duration")
            self.stream_url = info.get("url")
            self.headers = info.get("http_headers") or {}
            if not self.stream_url and info.get("requested_formats"):
                fmt = info["requested_formats"][0]
                self.stream_url, self.headers = fmt["url"], fmt.get("http_headers") or {}
        except Exception as e:  # yt-dlp raises many types
            err = e
        if not self.stream_url and self.media_url and self.media_url.startswith("http"):
            self.stream_url, self.headers = self.media_url, {}
        if not self.stream_url:
            raise MediaError(friendly_error(str(err or "")))
        if self.duration is None:
            self.duration = self._probe_duration()

    def _probe_duration(self):
        try:
            out = subprocess.run([ffmpeg_exe(), "-hide_banner", *self._input_args(0)],
                                 capture_output=True, text=True, timeout=30).stderr
            m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
            if m:
                h, mi, s = m.groups()
                return int(h) * 3600 + int(mi) * 60 + float(s)
        except Exception:
            pass
        return None

    def _input_args(self, start: float):
        if self.local_file:
            return ["-ss", f"{start:.3f}", "-i", str(self.local_file)]
        args = ["-ss", f"{start:.3f}"]
        if self.stream_url.startswith("http"):
            args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
            if self.headers:
                args += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())]
        return args + ["-i", self.stream_url]

    def _ffmpeg(self, start: float, seconds: float, sample_rate: int):
        import numpy as np

        cmd = [ffmpeg_exe(), "-nostdin", "-hide_banner", "-loglevel", "error",
               *self._input_args(max(0.0, start)), "-t", f"{seconds:.3f}",
               "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "pipe:1"]
        p = subprocess.run(cmd, capture_output=True, timeout=180)
        if p.returncode != 0 and not p.stdout:
            err = p.stderr.decode(errors="replace").strip().splitlines()
            raise MediaError(err[-1][:200] if err else "ffmpeg failed")  # last line: no signed-URL fragments
        return np.frombuffer(p.stdout, dtype="<f4").copy()

    def read(self, start: float, seconds: float, sample_rate: int):
        """-> float32 mono samples of [start, start+seconds) (shorter at the end).

        Reads straight from the network while the local copy is not there yet.
        Sites like YouTube sometimes refuse such direct reads (403); then the
        whole track is fetched by yt-dlp (which knows the site) and read from disk."""
        if not self.local_file and not self._remote_failed:
            try:
                return self._ffmpeg(start, seconds, sample_rate)
            except (MediaError, subprocess.TimeoutExpired):
                self._remote_failed = True
        if not self.local_file:
            self.start_background_download()
            if self._dl_thread:
                self._dl_thread.join(timeout=600)
        if not self.local_file:
            raise MediaError("دانلود صدای ویدیو ناموفق بود (سایت اجازه‌ی دریافت نداد یا اینترنت قطع است).")
        return self._ffmpeg(start, seconds, sample_rate)

    def start_background_download(self):
        """Keep a local copy of the audio track (faster, seek-proof)."""
        if self._dl_thread or not self.page_url.startswith("http"):
            return

        def run():
            from yt_dlp import YoutubeDL

            target = self.cache_dir / "audio.%(ext)s"
            try:
                with cookie_file(self.cookies, self.cache_dir) as cf, \
                        YoutubeDL(_ydl_opts({"outtmpl": str(target), "retries": 5, "cookiefile": cf})) as ydl:
                    info = ydl.extract_info(self.page_url, download=True)
                    path = Path(ydl.prepare_filename(info))
                if path.exists() and path.stat().st_size > 0:
                    self.local_file = path
            except Exception:
                pass  # streaming from the network keeps working

        self._dl_thread = threading.Thread(target=run, daemon=True, name="audio-download")
        self._dl_thread.start()


class LiveSource:
    """Audio the browser extension captures from the playing <video> itself, for
    pages whose stream no tool can fetch (players inside frames, blob/MSE
    streams, live streams). It arrives in ~1 s chunks tagged with the video time
    they were heard at; the dub follows a few seconds behind the picture.

    Same reading interface as AudioSource (read, duration, title, key)."""
    KEEP = 900.0  # seconds of audio kept in memory (~58 MB at 16 kHz)

    def __init__(self, title: str = ""):
        self.title = title
        self.key = None        # no stable id: nothing is cached across jobs
        self.duration = None   # unknown (a live stream may never end)
        self.local_file = None
        self.cookies = []
        self._chunks = []      # sorted [(t0, rate, float32 samples at 16 kHz)]
        self._lock = threading.Condition()
        self.fed_at = 0.0      # monotonic time of the last chunk

    def feed(self, t0: float, samples, rate: float = 1.0):
        import time

        import numpy as np

        if not len(samples):
            return
        samples = np.asarray(samples, dtype=np.float32)
        rate = min(4.0, max(0.25, float(rate or 1.0)))
        end = t0 + len(samples) / 16000 * rate
        with self._lock:
            # a chunk heard again (a seek back) replaces what was there
            self._chunks = [c for c in self._chunks if c[0] + len(c[2]) / 16000 * c[1] <= t0 + 0.05 or c[0] >= end - 0.05]
            self._chunks.append((float(t0), rate, samples))
            self._chunks.sort(key=lambda c: c[0])
            # keep what lies around where the video is now: after a seek far back the new
            # sound must stay (measuring from the furthest point heard threw it away)
            self._chunks = [c for c in self._chunks if abs(c[0] - t0) < self.KEEP]
            self.fed_at = time.monotonic()
            self._lock.notify_all()

    def spans(self, gap: float = 0.4):
        """Stretches of continuous audio: [(start, end)] in video seconds."""
        out = []
        with self._lock:
            for t0, rate, a in self._chunks:
                end = t0 + len(a) / 16000 * rate
                if out and t0 <= out[-1][1] + gap:
                    out[-1][1] = max(out[-1][1], end)
                else:
                    out.append([t0, end])
        return [tuple(s) for s in out]

    def rate_at(self, t: float) -> float:
        with self._lock:
            for t0, rate, a in self._chunks:
                if t0 - 0.05 <= t < t0 + len(a) / 16000 * rate:
                    return rate
        return 1.0

    def read(self, start: float, seconds: float, sample_rate: int = 16000):
        """Samples heard from video time start on (continuous audio only), at most
        seconds of video. Time is video time; with the video sped up the samples
        cover more video per second (see rate_at)."""
        import numpy as np

        parts, at = [], start
        with self._lock:
            for t0, rate, a in self._chunks:
                end = t0 + len(a) / 16000 * rate
                if end <= at + 0.01 or t0 >= start + seconds:
                    continue
                if t0 > at + 0.4:
                    break  # a hole: only continuous audio
                i = max(0, int((at - t0) / rate * 16000))
                j = min(len(a), int((start + seconds - t0) / rate * 16000))
                if j > i:
                    parts.append(a[i:j])
                    at = t0 + j / 16000 * rate
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    def wait(self, timeout: float):
        with self._lock:
            self._lock.wait(timeout=timeout)

    def start_background_download(self):
        pass
