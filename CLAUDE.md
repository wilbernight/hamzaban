# Hamzaban (هم‌زبان) — project memory

Offline Persian translation (Thunderbird e-mail) and live Persian video dubbing (Chrome) on the user's
Mac (Apple M5, 16 GB, macOS 27). **Read `HANDOFF.md` first**: architecture, decisions, test results,
known issues (numbered) and the next steps, in Persian.

## Talk to the user
- Reply in Persian, with correct Persian orthography (ZWNJ, Persian punctuation). The user is a PHP developer.
- The engine may be in use (the user watches dubs): warn before `./install.sh`, which restarts it.
- Ask before big downloads and before deleting anything (≈15 GB of leftovers is waiting for approval, see HANDOFF §8).

## Layout
- Source: this folder (`engine/`, `chrome/`, `thunderbird/`, `menubar/`, `install.sh`). A copy is kept in
  `~/Library/Application Support/Hamzaban/source`.
- Installed app: `~/Library/Application Support/Hamzaban` (`$APP`): `venv` (engine), `venv-omni`, `venv-aava`,
  `engine/`, `parsigo/`, `persian-writing/`, models, `logs/engine.log`, `config.json`, `glossary.txt`.
- Service: LaunchAgent `com.hamzaban.engine` → `python -m hamzaban` on 127.0.0.1:17860. Voice workers are
  child processes: Aava :17861 (`engine/voice_worker.py`), OmniVoice :17862 (`engine/omni_worker.py`).
- Menu-bar switch: `~/Applications/Hamzaban.app` (`--status|--on|--off` from a terminal).

## Commands
- Deploy source to the installed app: `./install.sh` (idempotent; `HAMZABAN_OMNI=1` / `HAMZABAN_AAVA=1` install optional voices).
- Tests (no models needed): `python3 -m unittest discover -s engine/tests`
  (add `HAMZABAN_PERSIAN_WORDS="$APP/persian-writing/assets/persian_words.txt"` for the dictionary test).
- Health: `curl -s http://127.0.0.1:17860/api/health`; logs: `tail -f "$APP/logs/engine.log"` (+ `omni.log`, `aava.log`).
- Chrome must be reloaded (↻ in chrome://extensions) after deploying extension changes.

## Conventions
- Anything on MLX (Whisper, Aava, OmniVoice) runs on a single dedicated thread (MLX streams are per thread).
- Heavy ML stacks live in their own venvs and talk to the engine over loopback HTTP (`hamzaban/workers.py`).
- The API only accepts browser-extension Origins or none (`server.LocalExtensionsOnly`); keep it that way.
- Translation cache stores raw model output; Persian clean-up (`persian.normalize`) and `glossary.apply` run on read.
- Recommended dub voices: `omni_female` / `omni_male` (OmniVoice = VoiceStudio's engine, via mlx-audio 0.5.7).
