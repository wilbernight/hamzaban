#!/bin/bash
# Removes the Hamzaban engine, its models and its background service.
# (The Thunderbird add-on and Chrome extension are removed from their own settings.)
set -uo pipefail

APP="$HOME/Library/Application Support/Hamzaban"
LABEL="com.hamzaban.engine"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "سرویس هم‌زبان متوقف و حذف شد."

read -r -p "پوشه‌ی برنامه و مدل‌های صدا/گفتار هم پاک شود؟ ($APP) [y/N] " a
[[ "$a" == [yY]* ]] && rm -rf "$APP" && echo "پاک شد."

read -r -p "مدل‌های ترجمه (TranslateGemma) از Ollama هم پاک شوند؟ [y/N] " b
if [[ "$b" == [yY]* ]]; then
  ollama rm translategemma:12b translategemma:4b 2>/dev/null
  echo "پاک شد."
fi
