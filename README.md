# Local Whisper App (Windows)

Local-only speech-to-text **push-to-talk** app for Windows.

- Electron tray app registers a global hotkey (**Ctrl+Alt+W** by default)
- Local Python service records microphone audio, runs **`faster-whisper`** offline, and returns text
- Electron pastes the transcript into **whatever app is focused** (clipboard + Ctrl+V)

## Prereqs

- **Windows 10/11**
- **Node.js** (LTS recommended)
- **Python 3.10+** (with `pip`)
- NVIDIA GPU optional (recommended). CPU also works.

## Setup

From `f:\\Projects\\AppDev\\local-whisper-app`:

```powershell
npm install
npm run python:install
```

## Run

```powershell
npm run dev
```

Then focus any app (Notepad, browser, etc), press **Ctrl+Alt+W**, speak a sentence or two, pause briefly, and it will paste the transcript.

## Windows shortcut / startup

- **Shortcut / Start menu / Taskbar**:
  - Create a shortcut to `start-local-whisper.bat` and pin it.
- **Start on Windows startup** (simple):
  - Press `Win+R` → type `shell:startup` → Enter
  - Put the shortcut to `start-local-whisper.bat` in that folder

## Configuration (environment variables)

- `LOCAL_WHISPER_HOTKEY` (default `Control+Alt+W`)
- `LOCAL_WHISPER_PYTHON` (default `py`) — set to a full path to python if needed
- `WHISPER_MODEL` (default `small.en`) — try `medium.en` for more accuracy
- `WHISPER_DEVICE` (default `cuda`) — set `cpu` to force CPU
- `WHISPER_COMPUTE_TYPE` (default `int8`) — examples: `float16`, `int8`, `float32`
- `WHISPER_MODEL_DIR` — where models are downloaded/cached
- `MAX_SILENCE_MS` (default `5000`)
- `MAX_RECORD_MS` (default `10000`)
- `MIN_RECORD_MS` (default `5000`)

## Notes / troubleshooting

- Some apps running **as Administrator** may block paste/SendKeys. If you need to paste into elevated apps, run this app elevated too.
- If you don’t see a tray icon, set `LOCAL_WHISPER_TRAY_ICON` to a valid `.png` path. By default it tries `garagetoadu/assets/logo.png` in your workspace.

