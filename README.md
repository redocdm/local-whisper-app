# Local Whisper App (Windows)

Local-only speech-to-text **hold-to-talk** tray app for Windows.

- Electron tray app — hold a global hotkey to record, release to transcribe and paste
- Local Python service runs **`faster-whisper`** or **`moonshine-voice`** fully offline
- Transcript is pasted into **whatever app is focused** (clipboard + Ctrl+V)
- Optional **voice assistant mode** — speaks responses via a local LLM (LM Studio)
- **Waveform overlay** shows recording/transcribing state with live level bars

## Prerequisites

- **Windows 10/11**
- **Node.js** LTS
- **Python 3.10** (exact — the venv is pinned to 3.10)
- NVIDIA GPU recommended (CUDA). CPU works with `WHISPER_DEVICE=cpu`.

## Setup

```powershell
npm install
npm run python:venv
npm run python:install
```

## Run

```powershell
npm run dev
```

Or double-click / shortcut `start-local-whisper.bat`.

Focus any app (Notepad, browser, VS Code…), **hold Ctrl+Alt+W**, speak, release — transcript is pasted where your cursor is.

---

## Features

### STT engines

Switch engines from the tray menu or set `LOCAL_WHISPER_STT_ENGINE`:

| Engine | Model | When to use |
|--------|-------|-------------|
| **Whisper** (default) | `faster-whisper` small.en on CUDA | Best accuracy, punctuation; ~1-2s post-release on GPU |
| **Moonshine** | `moonshine-voice` medium-streaming on CPU | Near-instant paste (streams during recording); lighter on GPU |

### Modes (toggle with Ctrl+Alt+M)

- **Simple STT** — transcribes and pastes
- **Voice assistant** — transcribes, sends to a local LLM, speaks the response via Windows SAPI. Requires [LM Studio](https://lmstudio.ai) running locally. The tray shows availability; the mode is disabled if LM Studio is unreachable.

### Waveform overlay

A small pill appears at the bottom of the screen while recording, with live level bars. State is reflected: Listening → Transcribing → Idle. Toggle in the tray menu under **Waveform overlay**.

### Sound cues

Audible cue on start/stop using Windows Speech On/Off sounds. Toggle in the tray menu under **Sound cues**.

---

## Windows shortcut / startup

**Pin to taskbar / Start menu:** create a shortcut to `start-local-whisper.bat`.

**Run at Windows startup:**
1. Press `Win+R` → type `shell:startup` → Enter
2. Drop the shortcut to `start-local-whisper.bat` in that folder

---

## Configuration

All settings are optional environment variables. Set them in your shell, a `.env` wrapper, or the `.bat` file.

### Hotkeys

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCAL_WHISPER_HOTKEY` | `Control+Alt+W` | Hold-to-talk key combo |
| `LOCAL_WHISPER_MODE_TOGGLE_HOTKEY` | `Control+Alt+M` | Toggle STT ↔ Assistant mode |
| `LOCAL_WHISPER_DEFAULT_MODE` | `stt` | Starting mode (`stt` or `assistant`) |

### Audio / recording

| Variable | Default | Description |
|----------|---------|-------------|
| `AUDIO_SAMPLE_RATE` | `16000` | Mic sample rate (Hz) |
| `MAX_RECORD_MS` | `120000` | Max hold duration (2 min) |
| `MAX_SILENCE_MS` | `5000` | Auto-stop after this much silence |
| `MIN_RECORD_MS` | `5000` | Minimum before silence auto-stop |
| `LOCAL_WHISPER_PERSISTENT_MIC` | `1` | Keep mic stream open between sessions (instant start). Set `0` to open per-session instead. |
| `PRE_ROLL_MS` | `250` | Audio captured before session starts (catches clipped word onsets) |
| `VAD_AGGRESSIVENESS` | `2` | WebRTC VAD level 0–3 |
| `VAD_FRAME_MS` | `30` | VAD frame size: 10, 20, or 30 |

### Whisper

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `small.en` | Model name — e.g. `medium.en`, `large-v3` |
| `WHISPER_DEVICE` | `cuda` | `cuda` or `cpu` |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8`, `float16`, `float32` |
| `WHISPER_MODEL_DIR` | *(huggingface cache)* | Where models are downloaded/cached |

### Moonshine

| Variable | Default | Description |
|----------|---------|-------------|
| `MOONSHINE_MODEL_DIR` | *(auto)* | Path to a local Moonshine model directory |
| `MOONSHINE_MODEL_ARCH` | `5` | Architecture enum (`5` = MEDIUM_STREAMING) |

### Assistant / LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BASE_URL` | `http://127.0.0.1:1234/v1` | LM Studio (or any OpenAI-compatible) base URL |
| `LLM_MODEL` | `meta-llama-3-8b-instruct` | Model identifier passed to the LLM |
| `LLM_TEMPERATURE` | `0.7` | Sampling temperature |
| `ASSISTANT_TTS_ENABLED` | `1` | Set `0` to disable voice responses |
| `ASSISTANT_SYSTEM_PROMPT` | *(built-in)* | Override the assistant system prompt |
| `ASSISTANT_SANDBOX_ROOT` | `python/sandbox` | Directory the assistant can read/write files in |

### App

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCAL_WHISPER_PYTHON` | *(venv auto-detected)* | Full path to python.exe if auto-detection fails |
| `LOCAL_WHISPER_SOUND` | `1` | Set `0` to disable sound cues entirely |
| `LOCAL_WHISPER_WS_URL` | `ws://127.0.0.1:8765` | WebSocket URL for the Python service |
| `LOCAL_WHISPER_TRAY_ICON` | `logo.png` | Path to a `.png` for the tray icon |
| `LOCAL_WHISPER_DEBUG_LOG_PATH` | `python/debug.log` | Debug JSONL log path |

---

## Troubleshooting

**No tray icon** — make sure `logo.png` exists at the repo root, or set `LOCAL_WHISPER_TRAY_ICON`.

**Paste doesn't work in some apps** — apps running as Administrator block SendKeys from non-elevated processes. Run this app elevated too, or use a different paste method.

**First transcription is slow** — Whisper and Moonshine warm up in the background after the server starts (~5–10s). After that, latency is low.

**Moonshine not working** — make sure `moonshine-voice` is installed: `npm run python:install`. The model downloads automatically on first use (~200MB).

**"Not connected yet" on hotkey press** — the Python service is still starting. Wait 2–3s and try again. If it persists, check that Python 3.10 is available (`py -3.10 --version`).

**GPU not used** — check that `WHISPER_DEVICE=cuda` and that CUDA/cuDNN drivers are installed. The app logs the selected device on startup.

---

## Architecture

```
Electron (tray + hotkeys + overlay)
    │  WebSocket  ws://127.0.0.1:8765
    └──► Python service (ws_server.py)
              ├── stt/recording.py   — persistent mic stream, pre-roll, VAD
              ├── stt/model.py       — faster-whisper / moonshine-voice
              ├── assistant/         — LLM agent (optional)
              ├── tts/sapi_tts.py    — Windows SAPI speech output
              └── memory/            — SQLite preference store
```

Everything runs locally. No data leaves the machine.
