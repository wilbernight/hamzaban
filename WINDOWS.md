# راهنمای ساخت نسخه‌ی ویندوز هم‌زبان

نسخه‌ی فعلی برای مک (Apple Silicon) نوشته شده است. بیشتر کد (موتور FastAPI، ترجمه با Ollama، پاک‌سازی نگارش
فارسی، واژه‌نامه، دوبله، هر دو افزونه) مستقل از سیستم‌عامل است و روی ویندوز بدون تغییر کار می‌کند. فقط چند بخش
به مک وابسته‌اند که در این سند آمده‌اند و باید جایگزین شوند.

## ۱. چه چیزهایی بدون تغییر کار می‌کنند

| بخش | فایل‌ها | توضیح |
|---|---|---|
| افزونه‌ی Chrome | `chrome/` | فقط با `http://127.0.0.1:17860` حرف می‌زند؛ روی ویندوز هم همان‌طور Load unpacked می‌شود |
| افزونه‌ی Thunderbird | `thunderbird/` | همان فایل `.xpi` روی ویندوز نصب می‌شود |
| API و سرور | `engine/hamzaban/server.py` | FastAPI + uvicorn |
| ترجمه | `translator.py`, `langs.py` | Ollama نسخه‌ی ویندوز دارد (https://ollama.com/download) و مدل‌های `translategemma` همان‌اند |
| نگارش فارسی | `persian.py`, `glossary.py`, `numbers.py`, `idioms.py`, `pronounce.py`, `speech.py`, `spoken.py` | پایتون خالص |
| ویدیو | `media.py` | `yt-dlp` و `imageio-ffmpeg` روی ویندوز هم هستند |
| صدای ParSiGo | `tts.py` | ONNX Runtime روی CPU؛ روی ویندوز کار می‌کند |
| آزمون‌ها | `engine/tests/` | `python -m unittest discover -s engine/tests` |

## ۲. چه چیزهایی باید جایگزین شوند

### الف) محل نصب — `engine/hamzaban/config.py`
`APP_HOME` پیش‌فرض `~/Library/Application Support/Hamzaban` است. روی ویندوز `%LOCALAPPDATA%\Hamzaban` بگذارید:

```python
import sys
_default = (Path(os.environ["LOCALAPPDATA"]) / "Hamzaban" if sys.platform == "win32"
            else Path.home() / "Library" / "Application Support" / "Hamzaban")
APP_HOME = Path(os.environ.get("HAMZABAN_HOME", _default))
```

(بدون تغییر کد هم می‌شود متغیر محیطی `HAMZABAN_HOME` را تنظیم کرد.)

### ب) تشخیص گفتار — `engine/hamzaban/asr.py`
Whisper با `mlx-whisper` اجرا می‌شود که فقط روی Apple Silicon است. جایگزین ویندوز:
[`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (CTranslate2؛ روی کارت NVIDIA با CUDA و روی CPU با int8).

- مدل: `large-v3-turbo` (یا `deepdml/faster-whisper-large-v3-turbo-ct2`).
- `word_timestamps=True` را بدهید؛ خروجی `segments[].words[]` را به همان قالبی تبدیل کنید که `Transcriber`
  الان از `mlx_whisper.transcribe` می‌گیرد (`start`, `end`, `word`) تا بقیه‌ی خط لوله (`dubbing.py`) دست نخورد.
- قاعده‌ی «همه‌ی کارهای MLX روی یک رشته» برای faster-whisper لازم نیست، ولی نگه داشتن یک رشته‌ی جدا ضرری ندارد.
- `mlx_cache_mb` در `config.py` روی ویندوز بی‌اثر است.

پیشنهاد: یک تابع کارخانه در `asr.py` که بر اساس `sys.platform` یکی از دو پیاده‌سازی را برگرداند.

### ج) صداهای اُمنی و آوا — `engine/omni_worker.py`, `engine/voice_worker.py`, `engine/aava_mlx.py`
هر دو روی MLX هستند (فقط مک). روی ویندوز:

- **OmniVoice** (صدای پیشنهادی): نسخه‌ی PyTorch اصلی [k2-fsa/OmniVoice](https://github.com/k2-fsa/OmniVoice) را
  در یک venv جدا (`venv-omni`) نصب کنید و در `omni_worker.py` به‌جای `mlx_audio` آن را صدا بزنید. رابط HTTP همان
  بماند (پورت 17862) تا `engine/hamzaban/omni.py` و `workers.py` تغییری نکنند. صداهای مرجع در
  `engine/voices/omni/` هستند.
- **Aava**: با `transformers` + `snac` روی PyTorch (CUDA) قابل اجراست؛ روی CPU خیلی کند است. می‌شود در نسخه‌ی
  اول ویندوز کنارش گذاشت.
- بدون کارت گرافیک، فقط ParSiGo (CPU) کافی است.

### د) دمای سیستم — `engine/hamzaban/thermal.py`
از `NSProcessInfo.thermalState` مک می‌خواند. روی ویندوز اگر خواندن شکست بخورد خودش «nominal» برمی‌گرداند و کاری
لازم نیست؛ فقط مطمئن شوید `ctypes.util.find_library("objc")` روی ویندوز `None` برمی‌گرداند و استثنا گرفته می‌شود.

### هـ) نصب‌کننده — `install.sh`
اسکریپت bash با Homebrew و `launchctl` است. برای ویندوز یک `install.ps1` بنویسید که همین مراحل را انجام دهد:

1. Ollama را نصب/اجرا کند (`winget install Ollama.Ollama`) و `ollama pull translategemma:4b` و `translategemma:12b`.
2. `uv` را نصب کند (`winget install astral-sh.uv`) و venv موتور را بسازد:
   `uv venv "%LOCALAPPDATA%\Hamzaban\venv"` و `uv pip install -r engine/requirements.txt`
   (در `requirements.txt` خط `mlx-whisper` را با شرط `; sys_platform == "darwin"` محدود کنید و
   `faster-whisper ; sys_platform == "win32"` اضافه کنید).
3. پوشه‌ی `engine/hamzaban` و فایل‌های worker را به `%LOCALAPPDATA%\Hamzaban\engine` کپی کند.
4. `nimaone/persian_tts` (ParSiGo) و `ali2000hos/persian-writing` را `git clone` کند (همان کاری که `install.sh` می‌کند).
5. افزونه‌ی Thunderbird را zip کند (`Compress-Archive thunderbird\* hamzaban-thunderbird.zip` و تغییر پسوند به `.xpi`).
6. سرویس پس‌زمینه را بسازد (بند «و»).

### و) سرویس پس‌زمینه — LaunchAgent `com.hamzaban.engine`
روی ویندوز یکی از این‌ها:

- **Task Scheduler** با تریگر «At log on» و دستور
  `"%LOCALAPPDATA%\Hamzaban\venv\Scripts\pythonw.exe" -m hamzaban` (پوشه‌ی کاری: `...\Hamzaban\engine`)؛ ساده‌ترین راه.
- یا [NSSM](https://nssm.cc) / [WinSW](https://github.com/winsw/winsw) برای سرویس واقعی با راه‌اندازی دوباره‌ی خودکار
  (معادل `KeepAlive`).

موتور باید فقط روی `127.0.0.1` گوش بدهد (همین الان در `config.py` هست). میان‌افزار امنیتی
`server.LocalExtensionsOnly` را تغییر ندهید: فقط Origin افزونه‌های مرورگر پذیرفته می‌شود.

### ز) برنامه‌ی نوار بالا — `menubar/HamzabanBar.swift`
مخصوص مک است. معادل ویندوز یک آیکون System Tray است، مثلاً با پایتون و
[`pystray`](https://github.com/moses-palmer/pystray): منوی «روشن/خاموش» که Task Scheduler را
`schtasks /Run` و `/End` می‌کند و وضعیت را از `http://127.0.0.1:17860/api/health` می‌خواند.

## ۳. ترتیب پیشنهادی کار

1. `HAMZABAN_HOME` را تنظیم کنید، Ollama را نصب کنید و موتور را با ParSiGo و بدون ASR بالا بیاورید؛
   ترجمه‌ی ایمیل در Thunderbird باید از همین حالا کار کند.
2. `asr.py` را با faster-whisper بنویسید؛ دوبله‌ی «فقط زیرنویس» و بعد «دوبله + زیرنویس» با ParSiGo را آزمایش کنید.
3. `install.ps1` و Task Scheduler.
4. OmniVoice روی PyTorch (اگر کارت NVIDIA دارید).
5. آیکون System Tray.

## ۴. نکته‌ها

- جزئیات معماری، تصمیم‌ها، نتیجه‌ی آزمون‌ها و مشکلات شناخته‌شده در `HANDOFF.md` است؛ پیش از تغییر بخوانید.
- حداقل سخت‌افزار پیشنهادی: ۱۶ گیگ رم؛ برای OmniVoice و Whisper سریع کارت NVIDIA با ۸ گیگ VRAM یا بیشتر.
- مسیرهای ویندوز را همیشه با `pathlib` بسازید، نه با رشته و `/`.
- `deno` و `yt-dlp-ejs` (برای یوتیوب) روی ویندوز هم نصب می‌شوند.
