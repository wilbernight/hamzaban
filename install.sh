#!/bin/bash
# Hamzaban (هم‌زبان) installer — macOS on Apple Silicon.
# Safe to run again: it updates what is there and skips finished downloads.
#
#   ./install.sh                 full install (e-mail model 12B + dubbing model 4B)
#   HAMZABAN_LITE=1 ./install.sh  lighter: the 4B model for both
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Library/Application Support/Hamzaban"
VENV="$APP/venv"
LABEL="com.hamzaban.engine"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT=17860
DUB_MODEL="translategemma:4b"
EMAIL_MODEL="translategemma:12b"
[[ "${HAMZABAN_LITE:-0}" == 1 ]] && EMAIL_MODEL="$DUB_MODEL"
WHISPER="mlx-community/whisper-large-v3-turbo-q4"  # 4-bit: same words and speed, 1.1 GB less memory
export HF_HOME="$APP/hf"

step() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
die() { printf "\n\033[1;31mخطا: %s\033[0m\n" "$*" >&2; exit 1; }

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || die "این نصب‌کننده برای مک‌های Apple Silicon است."
command -v brew >/dev/null || die "Homebrew لازم است (https://brew.sh)."
mkdir -p "$APP"/{cache,logs,voices,extensions,engine} "$HOME/Library/LaunchAgents"

# ---------------------------------------------------------------- Ollama
step "Ollama (اجرای مدل ترجمه)"
command -v ollama >/dev/null || brew install ollama
brew services start ollama >/dev/null 2>&1 || brew services restart ollama >/dev/null
for _ in $(seq 60); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
curl -sf http://127.0.0.1:11434/api/version >/dev/null || die "Ollama بالا نیامد."

# ---------------------------------------------------------------- Python
step "محیط پایتون و کتابخانه‌ها"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
[[ -x "$UV" ]] || { brew install uv; UV="$(command -v uv)"; }
[[ -x "$VENV/bin/python" ]] || "$UV" venv --python 3.12 "$VENV"
REQ_HASH="$(shasum "$SRC/engine/requirements.txt" | cut -d' ' -f1)"
if [[ "$(cat "$APP/.requirements.sha" 2>/dev/null)" != "$REQ_HASH" || "${HAMZABAN_UPGRADE:-0}" == 1 ]]; then
  "$UV" pip install --python "$VENV/bin/python" -U -r "$SRC/engine/requirements.txt"
  echo "$REQ_HASH" > "$APP/.requirements.sha"
fi
rsync -a --delete "$SRC/engine/hamzaban/" "$APP/engine/hamzaban/"
cp "$SRC/engine/voice_worker.py" "$SRC/engine/aava_mlx.py" "$SRC/engine/omni_worker.py" "$SRC/engine/setup_aava.py" "$APP/engine/"
# keep a copy of the source next to the app (the source folder may be temporary)
[[ "$SRC" == "$APP/source" ]] || rsync -a --delete --exclude "__pycache__" "$SRC/" "$APP/source/"
# the engine writes the user's saved word fixes back into this folder too (engine/hamzaban/words)
[[ "$SRC" == "$APP/source" ]] || printf '%s\n' "$SRC" > "$APP/source_dir.txt"

# ---------------------------------------------------------------- ParSiGo TTS
step "موتور صدای فارسی (ParSiGo — github.com/nimaone/persian_tts)"
if [[ -d "$APP/parsigo/.git" ]]; then
  git -C "$APP/parsigo" pull --ff-only -q || true
else
  git clone -q --depth 1 https://github.com/nimaone/persian_tts "$APP/parsigo"
fi

step "قواعد نگارش فارسی (persian-writing — github.com/ali2000hos/persian-writing)"
if [[ -d "$APP/persian-writing/.git" ]]; then
  git -C "$APP/persian-writing" pull --ff-only -q || true
else
  git clone -q --depth 1 https://github.com/ali2000hos/persian-writing "$APP/persian-writing"
fi

step "دانلود مدل‌های صدا و گفتار (Hugging Face)"
"$VENV/bin/python" - "$APP/parsigo" "$WHISPER" <<'PY'
import sys
from huggingface_hub import hf_hub_download, snapshot_download
parsigo, whisper = sys.argv[1], sys.argv[2]
snapshot_download("Nimaone/pocket-tts-farsi-v2-onnx", local_dir=f"{parsigo}/model/onnx",
                  allow_patterns=["*.onnx", "*.onnx.data", "*.npz", "manifest.json"])
for name in ("normalize_fa.py", "tokenizer_ph.model"):
    hf_hub_download("mehdi-hf/pocket-tts-farsi-v2", name, local_dir=f"{parsigo}/model/v2")
snapshot_download(whisper)
print("ok")
PY

# ---------------------------------------------------------------- optional: Aava natural voice
# HAMZABAN_AAVA=1 ./install.sh  - Orpheus-3B Persian (KEYHAN-A/aava-tts-persian-3b, Apache-2.0):
# downloads 7.6 GB once, keeps a 2 GB 4-bit MLX copy
if [[ "${HAMZABAN_AAVA:-0}" == 1 && ! -f "$APP/aava-mlx-4/config.json" ]]; then
  step "صدای طبیعی آوا (Orpheus فارسی)"
  [[ -x "$APP/venv-aava/bin/python" ]] || "$UV" venv --python 3.12 "$APP/venv-aava"
  "$UV" pip install --python "$APP/venv-aava/bin/python" mlx-lm snac torch soundfile numpy
  "$APP/venv-aava/bin/python" "$SRC/engine/setup_aava.py" "$APP"
  rm -rf "$APP/aava-model"  # the 4-bit copy is all the voice needs
fi

# ---------------------------------------------------------------- optional: OmniVoice (recommended voice)
# HAMZABAN_OMNI=1 ./install.sh  - k2-fsa OmniVoice (VoiceStudio's engine) on MLX via mlx-audio:
# 2 GB model; clear Persian, steady male/female voices, ~6x faster than real time on an M5
if [[ "${HAMZABAN_OMNI:-0}" == 1 && ! -f "$APP/omnivoice-bf16/config.json" ]]; then
  step "صدای اُمنی (OmniVoice)"
  [[ -x "$APP/venv-omni/bin/python" ]] || "$UV" venv --python 3.12 "$APP/venv-omni"
  "$UV" pip install --python "$APP/venv-omni/bin/python" "mlx-audio==0.5.7" soundfile
  "$APP/venv-omni/bin/python" -c "from huggingface_hub import snapshot_download as s; s('mlx-community/OmniVoice-bfloat16', local_dir='$APP/omnivoice-bf16')"
fi
# the steady male/female reference clips ship with the source
[[ -f "$APP/omnivoice-voices/voices.json" ]] || { mkdir -p "$APP/omnivoice-voices"; cp "$SRC/engine/voices/omni/"* "$APP/omnivoice-voices/"; }

# ---------------------------------------------------------------- translation models
step "دانلود مدل ترجمه (TranslateGemma)"
ollama pull "$DUB_MODEL"
[[ "$EMAIL_MODEL" == "$DUB_MODEL" ]] || ollama pull "$EMAIL_MODEL"

# ---------------------------------------------------------------- config
if [[ ! -f "$APP/config.json" ]]; then
  cat > "$APP/config.json" <<JSON
{
  "port": $PORT,
  "email_model": "$EMAIL_MODEL",
  "dub_model": "$DUB_MODEL",
  "whisper_model": "$WHISPER",
  "default_voice": "male_news"
}
JSON
else  # move existing installs to the 4-bit Whisper (same transcripts, a quarter of the memory)
  sed -i '' 's#"mlx-community/whisper-large-v3-turbo"#"mlx-community/whisper-large-v3-turbo-q4"#' "$APP/config.json"
fi

if [[ ! -f "$APP/glossary.txt" ]]; then
  cat > "$APP/glossary.txt" <<'TXT'
# واژه‌نامه‌ی شخصی هم‌زبان — هر خط یک اصطلاح (بعد از ذخیره فوراً اعمال می‌شود):
#   اصطلاح انگلیسی = معادل دلخواه = ترجمه‌های اشتباهی که باید عوض شوند (با ، جدا کنید)
# مثال:
# service container = سرویس کانتینر = ظرف سرویس، محفظه‌ی سرویس
# Eloquent = الوکوئنت = فصیح
TXT
fi

# ---------------------------------------------------------------- background service
step "راه‌اندازی موتور هم‌زبان (با روشن شدن مک خودکار اجرا می‌شود)"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$VENV/bin/python</string><string>-m</string><string>hamzaban</string></array>
  <key>WorkingDirectory</key><string>$APP/engine</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>$APP/engine</string>
    <key>HF_HOME</key><string>$HF_HOME</string>
    <key>HF_HUB_OFFLINE</key><string>1</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>$VENV/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$APP/logs/engine.log</string>
  <key>StandardErrorPath</key><string>$APP/logs/engine.log</string>
</dict>
</plist>
PLIST
USER_DISABLED=0
launchctl print-disabled "gui/$(id -u)" | grep -q "\"$LABEL\" => disabled" && USER_DISABLED=1
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  # bootout returns before the old process is gone
  for _ in $(seq 20); do launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break; sleep 0.5; done
  WAS_RUNNING=1
else
  WAS_RUNNING=0
fi
# switched off from the menu-bar app: keep it off, only the files are updated
if [[ $USER_DISABLED == 0 || $WAS_RUNNING == 1 ]]; then
  [[ $USER_DISABLED == 1 ]] && launchctl enable "gui/$(id -u)/$LABEL"
  for attempt in 1 2 3; do
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break
    [[ $attempt == 3 ]] && die "راه‌اندازی سرویس ناموفق بود (launchctl bootstrap)."
    sleep 2
  done
  [[ $USER_DISABLED == 1 ]] && launchctl disable "gui/$(id -u)/$LABEL"
fi

# ---------------------------------------------------------------- menu-bar switch
if command -v swiftc >/dev/null; then
  step "برنامه‌ی نوار بالای مک (روشن/خاموش کردن موتور)"
  "$SRC/menubar/build.sh" >/dev/null && open "$HOME/Applications/Hamzaban.app" || true
fi

# ---------------------------------------------------------------- extensions
step "آماده‌سازی افزونه‌ها"
rm -rf "$APP/extensions/chrome"
cp -R "$SRC/chrome" "$APP/extensions/chrome"
rm -f "$APP/extensions/hamzaban-thunderbird.xpi"
(cd "$SRC/thunderbird" && zip -qr -X "$APP/extensions/hamzaban-thunderbird.xpi" . -x ".*")

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  for _ in $(seq 90); do curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null && break; sleep 1; done
  curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null || die "موتور بالا نیامد؛ لاگ: $APP/logs/engine.log"
else
  echo "موتور خاموش مانده (از برنامه‌ی نوار بالا خاموشش کرده بودید)؛ هر وقت خواستید از همان‌جا روشنش کنید."
fi

cat <<DONE

✅ هم‌زبان نصب شد. روشن و خاموش کردنش: آیکون هم‌زبان در نوار بالای صفحه‌ی مک.

Thunderbird:
  منوی ☰ → Add-ons and Themes → چرخ‌دنده → Install Add-on From File…
  فایل:  $APP/extensions/hamzaban-thunderbird.xpi

Chrome:
  آدرس chrome://extensions → Developer mode را روشن کنید → Load unpacked
  پوشه:  $APP/extensions/chrome
DONE
open -R "$APP/extensions/hamzaban-thunderbird.xpi" 2>/dev/null || true
