# macOS Porting Notes

This document tracks the changes required to run Local Whisper App on macOS as a
single cross-platform repo (one branch, runtime platform detection, no fork).
No code changes have been made yet — this is the implementation reference.

## Prerequisites (macOS)

- **macOS 13+** recommended
- **Node.js** LTS
- **Python 3.10**
- **Xcode Command Line Tools** (`xcode-select --install`) — needed to build `webrtcvad`,
  which has no prebuilt macOS arm64 wheels
- **Accessibility permission** granted to the app (Terminal/Electron) in
  System Settings → Privacy & Security → Accessibility — required by BOTH the
  global hotkey hook (`uiohook-napi`) and synthetic Cmd+V paste

---

## Architecture: how one repo supports both platforms

**Electron:** all four PowerShell call sites in `main.cjs` (persistent helper,
beep fallback, `speakText`, paste fallback) move behind a single
`electron/platform.cjs` module that exports one object per platform:

```js
// electron/platform.cjs
module.exports = process.platform === "darwin"
  ? {
      helperCmd: "bash",
      helperArgs: ["-c", MAC_HELPER_SCRIPT],
      speak: (text) => spawnSay(text),          // `say`
      pasteFallback: () => spawnOsascriptPaste(),
      venvPython: "python/.venv/bin/python",
      fallbackPython: "python3.10",
      defaultDevice: "cpu",
    }
  : {
      helperCmd: "powershell",
      helperArgs: ["-NoProfile", "-NonInteractive", "-Command", WIN_HELPER_SCRIPT],
      speak: (text) => spawnSapiSpeak(text),     // SAPI
      pasteFallback: () => spawnSendKeysPaste(),
      venvPython: "python/.venv/Scripts/python.exe",
      fallbackPython: "py",
      defaultDevice: "cuda",
    };
```

`main.cjs` imports this once; no `if (isMac)` scattered through the logic.

**Python:** one dispatch point per platform-specific module, selected on `sys.platform`:

```python
# python/tts/__init__.py
import sys
if sys.platform == "darwin":
    from tts.macos_tts import speak
else:
    from tts.sapi_tts import speak
```

Callers do `from tts import speak` and never see the platform.

---

## Required changes, by file

### 1. `electron/main.cjs` — four PowerShell call sites

| Site | Windows (current) | macOS replacement |
|------|-------------------|-------------------|
| Persistent helper (paste + cues) | PowerShell + SendKeys + SoundPlayer | `bash` loop reading stdin; `osascript` for paste, `afplay` for cues |
| `playCue` fallback | one-shot `[console]::beep` | `afplay /System/Library/Sounds/Tink.aiff` (start) / `Pop.aiff` (end) |
| `speakText` (mode announcements) | PowerShell SAPI | `say "text"` |
| `pasteIntoFocusedApp` fallback | PowerShell SendKeys | `osascript -e 'tell application "System Events" to keystroke "v" using command down'` |

macOS helper script body:

```bash
while IFS= read -r line; do
  case "$line" in
    paste)      osascript -e 'tell application "System Events" to keystroke "v" using command down' ;;
    beep:start) afplay /System/Library/Sounds/Tink.aiff & ;;
    beep:end)   afplay /System/Library/Sounds/Pop.aiff & ;;
  esac
done
```

Also in `main.cjs`: the spawned-Python resolution is Windows-only —
`python/.venv/Scripts/python.exe` and the `"py"` launcher fallback must come from
`platform.cjs` (`python/.venv/bin/python` and `python3.10` on macOS), as must the
`WHISPER_DEVICE` default passed to the Python process env.

### 2. `python/tts/macos_tts.py` — new file

```python
import os
import subprocess


def speak(text: str) -> None:
    if (os.getenv("ASSISTANT_TTS_ENABLED", "1") or "1") == "0":
        return
    text = (text or "").strip()
    if not text:
        return
    subprocess.run(["say", text], check=False, timeout=120)
```

Plus the dispatch in `python/tts/__init__.py` (see above), and `ws_server.py`
switches its import to `from tts import speak`.

### 3. Whisper device default — `python/stt/settings.py`

**Important correction:** faster-whisper is built on CTranslate2, which supports
**CPU and CUDA only — there is no Metal/MPS backend.** On Apple Silicon it runs
on CPU via Apple Accelerate (decent for `small.en`, but not GPU-fast).

```python
import sys
device: str = os.getenv("WHISPER_DEVICE", "cpu" if sys.platform == "darwin" else "cuda")
```

Practical guidance for Mac users:
- **Moonshine is the better default on macOS** — it is CPU-native, streams during
  recording, and its post-release latency is near zero.
- For Whisper-quality output on Apple Silicon GPU, a future option is swapping the
  backend to `mlx-whisper` or `whisper.cpp` — out of scope for the initial port.

### 4. `python/stt/cudnn.py`

Already guarded with `if sys.platform != "win32": return`. **No change needed.** ✅

### 5. `package.json` scripts — currently Windows-only

`py -3.10` and `python\.venv\Scripts\python.exe` fail on macOS. Most elegant fix:
replace the three scripts with one small Node launcher (Node is already a
prerequisite) that resolves the right interpreter/paths per platform:

```json
"python:venv":    "node scripts/py.cjs venv",
"python:install": "node scripts/py.cjs install",
"python:run":     "node scripts/py.cjs run"
```

`scripts/py.cjs` picks `py -3.10` / `python3.10` and `Scripts/` / `bin/` based on
`process.platform` (~20 lines, reusing the same constants as `electron/platform.cjs`).

### 6. Launcher — `start-local-whisper.command` (new file)

```bash
#!/bin/bash
cd "$(dirname "$0")"
npm run dev
```

`chmod +x start-local-whisper.command` (double-clickable in Finder).
`start-local-whisper.bat` stays for Windows.

### 7. Hotkeys — `uiohook-napi`

Has macOS prebuilds (incl. arm64); works once Accessibility permission is granted.
Without permission, keydown/keyup silently never fire — the app should detect this
on macOS and show a notification pointing at
System Settings → Privacy & Security → Accessibility.

---

## Dependency portability check

| Package | macOS status |
|---------|--------------|
| `faster-whisper` / `ctranslate2` | ✅ wheels for macOS arm64/x86_64 (CPU only) |
| `moonshine-voice` | ✅ supports macOS |
| `sounddevice` | ✅ bundles PortAudio |
| `webrtcvad` | ⚠️ builds from source — needs Xcode CLT |
| `websockets`, `numpy`, etc. | ✅ |
| `uiohook-napi` | ✅ prebuilds; needs Accessibility permission |
| Electron (tray, overlay, clipboard, Notification) | ✅ cross-platform |

Everything else (WebSocket server, recording/pre-roll, streaming transcription,
LLM agent, sandbox FS, overlay HTML) is already platform-agnostic.

## Summary of files to touch

| File | Change |
|------|--------|
| `electron/platform.cjs` | **New** — all platform constants/commands in one place |
| `electron/main.cjs` | Route 4 PowerShell sites + python/device resolution through `platform.cjs` |
| `python/tts/macos_tts.py` | **New** — `say`-based TTS |
| `python/tts/__init__.py` | Platform dispatch |
| `python/ws_server.py` | Import `from tts import speak` |
| `python/stt/settings.py` | Platform-aware `WHISPER_DEVICE` default |
| `scripts/py.cjs` | **New** — cross-platform npm script launcher |
| `package.json` | Point python:* scripts at `scripts/py.cjs` |
| `start-local-whisper.command` | **New** — macOS launcher |

Estimated effort: **~1 day**, plus testing on real Mac hardware (especially
Accessibility permission flows and paste into various apps).
