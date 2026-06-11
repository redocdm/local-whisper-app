# macOS Porting Notes

This document tracks the changes required to run Local Whisper App on macOS.
No code changes have been made yet — this is a reference for when Mac support is implemented.

## Prerequisites (macOS)

- **macOS 12 Ventura or later** recommended
- **Node.js** LTS
- **Python 3.10**
- Apple Silicon (M1/M2/M3) or Intel. Apple Silicon can use MPS acceleration.
- **Accessibility permission** must be granted to the app in:
  System Settings → Privacy & Security → Accessibility

---

## Required code changes

### 1. Paste — `electron/main.cjs`

Replace the PowerShell `SendKeys` helper with an `osascript` call.

**Current (Windows):**
```powershell
[System.Windows.Forms.SendKeys]::SendWait('^v')
```

**macOS replacement:**
```bash
osascript -e 'tell application "System Events" to keystroke "v" using command down'
```

The long-lived helper process concept stays the same — just swap the script body and use `bash` instead of `powershell` as the process.

---

### 2. Sound cues — `electron/main.cjs`

Replace `SoundPlayer` / `Speech On.wav` (Windows-only) with `afplay`.

**macOS replacement in helper script:**
```bash
case "$line" in
  beep:start) afplay /System/Library/Sounds/Tink.aiff ;;
  beep:end)   afplay /System/Library/Sounds/Pop.aiff ;;
esac
```

Alternatively use Electron's `shell.beep()` as a cross-platform baseline (no WAV needed).

---

### 3. TTS — `python/tts/sapi_tts.py`

Windows SAPI (`System.Speech`) is not available on macOS. Replace with the built-in `say` command.

**macOS replacement (`python/tts/sapi_tts.py` or a new `macos_tts.py`):**
```python
import subprocess, os

def speak(text: str) -> None:
    if os.getenv("ASSISTANT_TTS_ENABLED", "1") == "0":
        return
    text = (text or "").strip()
    if not text:
        return
    subprocess.run(["say", text], check=False)
```

The TTS module should detect platform and dispatch accordingly:
```python
import sys
if sys.platform == "darwin":
    from tts.macos_tts import speak
else:
    from tts.sapi_tts import speak
```

---

### 4. Whisper device — `electron/main.cjs` / `python/stt/settings.py`

CUDA is not available on macOS. Default `WHISPER_DEVICE` should be `auto` on macOS
(faster-whisper supports MPS on Apple Silicon with recent versions) or `cpu` as a safe fallback.

```python
import sys
default_device = "auto" if sys.platform == "darwin" else "cuda"
```

Compute type: use `float32` or `int8` on CPU/MPS.

---

### 5. cuDNN DLL setup — `python/stt/cudnn.py`

Already a no-op on non-Windows (the function checks the platform before adding DLL paths).
**No change needed.**

---

### 6. Hotkey / uiohook-napi

`uiohook-napi` works on macOS but the app must be granted **Accessibility permission**.
Without it, `keydown`/`keyup` events are not delivered and hold-to-talk silently fails.

The app should detect failure and show a notification:
> "Hold-to-talk requires Accessibility access. Go to System Settings → Privacy & Security → Accessibility and enable Local Whisper."

No code change to `uiohook-napi` itself — just a better error message on macOS.

---

### 7. `start-local-whisper.bat`

Not applicable on macOS. Provide a `start-local-whisper.command` (double-clickable shell script) or a `start-local-whisper.sh`:

```bash
#!/bin/bash
cd "$(dirname "$0")"
npm run dev
```

Mark executable: `chmod +x start-local-whisper.command`

---

## Summary of files to change

| File | Change |
|------|--------|
| `electron/main.cjs` | Platform-switch paste helper and sound cues; default device |
| `python/tts/sapi_tts.py` | Platform dispatch to `say` on macOS |
| `python/stt/settings.py` | Default `WHISPER_DEVICE` to `auto` on macOS |
| `start-local-whisper.command` | New file — macOS launcher |

All other files (WebSocket server, recording, Moonshine, LLM agent, overlay HTML, sandbox FS) are already platform-agnostic.

Estimated effort: **~1 day** of focused work.
