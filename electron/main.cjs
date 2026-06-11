const { app, globalShortcut, Notification, clipboard, Menu, Tray, BrowserWindow, screen } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const WebSocket = require("ws");
let uIOhook;
let UiohookKey;
try {
  ({ uIOhook, UiohookKey } = require("uiohook-napi"));
} catch {
  uIOhook = null;
  UiohookKey = null;
}

const WS_URL = process.env.LOCAL_WHISPER_WS_URL || "ws://127.0.0.1:8765";
const HOTKEY = process.env.LOCAL_WHISPER_HOTKEY || "Control+Alt+W";
const MODE_TOGGLE_HOTKEY =
  process.env.LOCAL_WHISPER_MODE_TOGGLE_HOTKEY || "Control+Alt+M";
const DEFAULT_MODE = (process.env.LOCAL_WHISPER_DEFAULT_MODE || "stt").toLowerCase();
const TRAY_ICON_PATH =
  process.env.LOCAL_WHISPER_TRAY_ICON ||
  path.resolve(__dirname, "..", "logo.png");

let tray = null;
let ws = null;
let wsConnected = false;
let pythonProc = null;
let reconnectTimer = null;
let reconnectDelayMs = 250;
let holdActive = false;
let wasActive = false;
let llmAvailable = false; // Whether LM Studio is reachable
let helperProc = null; // Long-lived PowerShell worker for paste/beep (avoids per-call spawn cost)
let overlayWin = null; // Pre-created waveform overlay window
let soundCuesEnabled = true;
let overlayEnabled = true;

const OVERLAY_WIDTH = 320;
const OVERLAY_HEIGHT = 76;

/** @typedef {"stt" | "assistant"} AppMode */
/** @type {AppMode} */
let currentMode = DEFAULT_MODE === "assistant" ? "assistant" : "stt";

/** @typedef {"whisper" | "moonshine"} SttEngine */
/** @type {SttEngine} */
let currentSttEngine = "whisper";

function debugLog(location, message, data) {
  // Opt-in only; avoid unexpected outbound requests.
  const ingestUrl = process.env.LOCAL_WHISPER_DEBUG_INGEST_URL;
  if (!ingestUrl) return;
  try {
    fetch(ingestUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        location,
        message,
        data,
        timestamp: Date.now(),
        sessionId: "debug-session",
        runId: process.env.LOCAL_WHISPER_RUN_ID || "run"
      })
    }).catch(() => {});
  } catch {}
}

function getSettingsPath() {
  return path.join(app.getPath("userData"), "settings.json");
}

function loadSettings() {
  try {
    const p = getSettingsPath();
    if (!fs.existsSync(p)) return;
    const raw = fs.readFileSync(p, "utf8");
    const parsed = JSON.parse(raw);
    if (parsed && (parsed.mode === "stt" || parsed.mode === "assistant")) {
      currentMode = parsed.mode;
    }
    if (parsed && (parsed.stt_engine === "whisper" || parsed.stt_engine === "moonshine")) {
      currentSttEngine = parsed.stt_engine;
    }
    if (parsed && typeof parsed.sound_cues === "boolean") {
      soundCuesEnabled = parsed.sound_cues;
    }
    if (parsed && typeof parsed.overlay_enabled === "boolean") {
      overlayEnabled = parsed.overlay_enabled;
    }
  } catch {}
}

function saveSettings() {
  try {
    const p = getSettingsPath();
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(
      p,
      JSON.stringify(
        {
          mode: currentMode,
          stt_engine: currentSttEngine,
          sound_cues: soundCuesEnabled,
          overlay_enabled: overlayEnabled
        },
        null,
        2
      ),
      "utf8"
    );
  } catch {}
}

function modeLabel(mode) {
  return mode === "assistant" ? "Voice Command" : "Simple STT";
}

function engineLabel(engine) {
  return engine === "moonshine" ? "Moonshine" : "Whisper";
}

function setSttEngine(engine) {
  if (engine !== "whisper" && engine !== "moonshine") return;
  if (currentSttEngine === engine) return;
  currentSttEngine = engine;
  saveSettings();
  updateTrayMenu();
  notify("Local Whisper", `STT Engine: ${engineLabel(currentSttEngine)}`);
}

function setMode(mode) {
  if (mode !== "stt" && mode !== "assistant") return;
  if (currentMode === mode) return;
  
  // Prevent switching to assistant mode if LLM is unavailable
  if (mode === "assistant" && !llmAvailable) {
    notify("Local Whisper", "LM Studio server not reachable. Assistant mode unavailable.");
    return;
  }
  
  currentMode = mode;
  saveSettings();
  updateTrayMenu();
  notify("Local Whisper", `Mode: ${modeLabel(currentMode)}`);
  
  // Announce mode change audibly
  const announcement = mode === "assistant" 
    ? "Voice assistant mode" 
    : "Speech to text mode";
  speakText(announcement);
}

function toggleMode() {
  // If trying to toggle to assistant but LLM unavailable, stay in STT
  if (currentMode === "stt" && !llmAvailable) {
    notify("Local Whisper", "LM Studio server not reachable. Assistant mode unavailable.");
    return;
  }
  setMode(currentMode === "stt" ? "assistant" : "stt");
}

function notify(title, body) {
  try {
    new Notification({ title, body }).show();
  } catch {
    // Notifications can fail in some environments; ignore.
  }
}

// One hidden PowerShell process handles all paste/beep commands. Loading
// System.Windows.Forms once here is what removes the ~0.5-1.5s per-paste cost.
// Cues use Windows' built-in Speech On/Off WAVs through the normal audio path;
// [console]::beep routes through the legacy Beep API, which is often inaudible.
const HELPER_SCRIPT = [
  "$ErrorActionPreference='SilentlyContinue';",
  "Add-Type -AssemblyName System.Windows.Forms;",
  "$startSound = $null; $endSound = $null;",
  "if (Test-Path 'C:\\Windows\\Media\\Speech On.wav') {",
  "  $startSound = New-Object System.Media.SoundPlayer 'C:\\Windows\\Media\\Speech On.wav'; $startSound.Load();",
  "}",
  "if (Test-Path 'C:\\Windows\\Media\\Speech Off.wav') {",
  "  $endSound = New-Object System.Media.SoundPlayer 'C:\\Windows\\Media\\Speech Off.wav'; $endSound.Load();",
  "}",
  "while ($true) {",
  "  $line = [Console]::In.ReadLine();",
  "  if ($null -eq $line) { break }",
  "  switch ($line) {",
  "    'paste' { [System.Windows.Forms.SendKeys]::SendWait('^v') }",
  "    'beep:start' { if ($startSound) { $startSound.Play() } else { [console]::beep(880,60) } }",
  "    'beep:end' { if ($endSound) { $endSound.Play() } else { [console]::beep(660,60) } }",
  "  }",
  "}"
].join(" ");

function ensureHelper() {
  if (helperProc && !helperProc.killed && helperProc.exitCode === null) return helperProc;
  try {
    helperProc = spawn(
      "powershell",
      ["-NoProfile", "-NonInteractive", "-Command", HELPER_SCRIPT],
      { windowsHide: true, stdio: ["pipe", "ignore", "ignore"] }
    );
    helperProc.on("error", () => {
      helperProc = null;
    });
    helperProc.on("exit", () => {
      helperProc = null;
    });
    return helperProc;
  } catch {
    helperProc = null;
    return null;
  }
}

function sendHelperCommand(command) {
  const proc = ensureHelper();
  if (!proc || !proc.stdin || !proc.stdin.writable) return false;
  try {
    proc.stdin.write(`${command}\n`, "utf8");
    return true;
  } catch {
    return false;
  }
}

function playCue(kind) {
  if (!soundCuesEnabled) return;
  const enabled = (process.env.LOCAL_WHISPER_SOUND || "1") !== "0";
  if (!enabled) return;

  debugLog("electron/main.cjs:playCue", "play cue", { kind });
  if (sendHelperCommand(kind === "start" ? "beep:start" : "beep:end")) return;

  // Fallback: one-shot spawn if the helper is unavailable.
  const cmd = kind === "start" ? "[console]::beep(880,60)" : "[console]::beep(660,60)";
  try {
    const ps = spawn("powershell", ["-NoProfile", "-Command", cmd], { windowsHide: true, stdio: "ignore" });
    ps.on("error", () => {});
  } catch {}
}

function speakText(text) {
  // Use Windows SAPI via PowerShell (same approach as Python TTS)
  const script = (
    "$t=[Console]::In.ReadToEnd();" +
    "Add-Type -AssemblyName System.Speech;" +
    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;" +
    "$s.Speak($t);"
  );
  
  try {
    const ps = spawn(
      "powershell",
      ["-NoProfile", "-NonInteractive", "-Command", script],
      { windowsHide: true, stdio: ["pipe", "ignore", "ignore"] }
    );
    ps.stdin.write(text, "utf8");
    ps.stdin.end();
    ps.on("error", () => {}); // Best-effort only
  } catch {
    // Silently fail if TTS unavailable
  }
}

function startPythonService() {
  debugLog("electron/main.cjs:startPythonService", "startPythonService called", {
    wsUrl: WS_URL,
    hasPythonProc: !!pythonProc,
    pythonProcKilled: pythonProc ? !!pythonProc.killed : null
  });
  if (pythonProc && !pythonProc.killed) return;

  const pythonEntry = path.resolve(__dirname, "..", "python", "server.py");
  const venvPython = path.resolve(__dirname, "..", "python", ".venv", "Scripts", "python.exe");
  const pythonExe = process.env.LOCAL_WHISPER_PYTHON || (fs.existsSync(venvPython) ? venvPython : "py");
  const pythonArgs = pythonExe.toLowerCase() === "py" ? ["-3.10", pythonEntry] : [pythonEntry];

  pythonProc = spawn(pythonExe, pythonArgs, {
    cwd: path.resolve(__dirname, ".."),
    env: {
      ...process.env,
      // Default to best settings for GTX 10xx: CUDA + INT8.
      // Note: WHISPER_DEVICE/WHISPER_COMPUTE_TYPE can be overridden via the optional LOCAL_WHISPER_* vars.
      WHISPER_DEVICE: process.env.LOCAL_WHISPER_DEVICE || "cuda",
      WHISPER_COMPUTE_TYPE: process.env.LOCAL_WHISPER_COMPUTE_TYPE || "int8",
      WHISPER_MODEL: process.env.WHISPER_MODEL || "small.en"
    },
    stdio: "pipe",
    windowsHide: true
  });

  debugLog("electron/main.cjs:startPythonService", "python spawned", {
    pid: pythonProc.pid,
    pythonExe,
    pythonArgs
  });

  pythonProc.stdout.on("data", (d) => process.stdout.write(`[py] ${d}`));
  pythonProc.stderr.on("data", (d) => process.stderr.write(`[py] ${d}`));
  pythonProc.on("exit", (code) => {
    debugLog("electron/main.cjs:pythonExit", "python exited", {
      code,
      hadWsConnected: wsConnected,
      wsUrl: WS_URL
    });
    pythonProc = null;
    // If the websocket is still connected, another server instance owns the
    // port (our spawn lost the bind race) — keep using it, don't respawn.
    if (wsConnected) return;
    notify("Local Whisper", `STT service stopped (code ${code}).`);
    scheduleReconnect();
  });
}

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  debugLog("electron/main.cjs:connectWebSocket", "connectWebSocket attempt", {
    wsUrl: WS_URL,
    hasPythonProc: !!pythonProc
  });

  wsConnected = false;
  ws = new WebSocket(WS_URL);

  ws.on("open", () => {
    debugLog("electron/main.cjs:wsOpen", "websocket open", { wsUrl: WS_URL });
    wsConnected = true;
    reconnectDelayMs = 250;
    notify("Local Whisper", `Ready (${modeLabel(currentMode)}). Hold ${HOTKEY} to talk.`);
  });

  ws.on("message", async (data) => {
    let msg;
    try {
      msg = JSON.parse(data.toString("utf8"));
    } catch {
      return;
    }

    if (msg.type === "status") {
      if (msg.state === "listening") {
        wasActive = true;
        showOverlay("listening");
        notify("Local Whisper", "Listening…");
      }
      if (msg.state === "transcribing") {
        wasActive = true;
        showOverlay("transcribing");
        notify("Local Whisper", "Transcribing…");
      }
      if (msg.state === "thinking") {
        wasActive = true;
        showOverlay("thinking");
        notify("Local Whisper", "Thinking…");
      }
      if (msg.state === "speaking") {
        wasActive = true;
        showOverlay("speaking");
        notify("Local Whisper", "Speaking…");
      }
      if (msg.state === "idle") {
        if (wasActive) playCue("end");
        wasActive = false;
        hideOverlay();
        notify("Local Whisper", `Ready (${modeLabel(currentMode)}).`);
      }
      return;
    }

    if (msg.type === "level" && typeof msg.value === "number") {
      overlaySend("level", msg.value);
      return;
    }

    if (msg.type === "result" && typeof msg.text === "string") {
      const text = msg.text.trim();
      if (!text) return;

      await pasteIntoFocusedApp(text);
      notify("Local Whisper", "Pasted transcript.");
      return;
    }

    if (msg.type === "assistant_result" && typeof msg.text === "string") {
      const text = msg.text.trim();
      if (!text) return;
      // Voice mode: Python speaks. Optionally show a short preview.
      const preview = text.length > 140 ? `${text.slice(0, 140)}…` : text;
      notify("Assistant", preview);
      return;
    }

    if (msg.type === "llm_status" && typeof msg.available === "boolean") {
      llmAvailable = msg.available;
      // If we're in assistant mode but LLM becomes unavailable, switch to STT
      if (currentMode === "assistant" && !llmAvailable) {
        setMode("stt");
        notify("Local Whisper", "LM Studio unavailable. Switched to STT mode.");
      }
      updateTrayMenu();
    }

    if (msg.type === "error" && typeof msg.message === "string") {
      notify("Local Whisper (error)", msg.message);
    }
  });

  ws.on("close", () => {
    wsConnected = false;
    scheduleReconnect();
  });

  ws.on("error", (err) => {
    debugLog("electron/main.cjs:wsError", "websocket error", {
      wsUrl: WS_URL,
      errorMessage: err ? err.message : null,
      errorCode: err ? err.code : null
    });
    wsConnected = false;
    scheduleReconnect();
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  debugLog("electron/main.cjs:scheduleReconnect", "scheduleReconnect set", {
    wsUrl: WS_URL,
    hasPythonProc: !!pythonProc,
    delayMs: reconnectDelayMs
  });
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (wsConnected) return;
    startPythonService();
    connectWebSocket();
  }, reconnectDelayMs);
  // Back off while the Python service boots (reset to 250ms on successful connect).
  reconnectDelayMs = Math.min(Math.round(reconnectDelayMs * 1.6), 3000);
}

function sendStartCommand() {
  debugLog("electron/main.cjs:sendStartCommand", "hotkey triggered -> send start", {
    wsConnected,
    wsReadyState: ws ? ws.readyState : null,
    hotkey: HOTKEY,
    mode: currentMode
  });
  if (holdActive) return;
  holdActive = true;
  if (!wsConnected || !ws || ws.readyState !== WebSocket.OPEN) {
    notify("Local Whisper", "Not connected yet—starting service…");
    scheduleReconnect();
    return;
  }
  playCue("start");
  ws.send(JSON.stringify({ type: "start", mode: currentMode, stt_engine: currentSttEngine }));
}

function sendStopCommand() {
  if (!holdActive) return;
  holdActive = false;
  debugLog("electron/main.cjs:sendStopCommand", "hotkey released -> send stop", {
    wsConnected,
    wsReadyState: ws ? ws.readyState : null,
    hotkey: HOTKEY,
    mode: currentMode
  });
  if (!wsConnected || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ type: "stop" }));
}

function parseHoldHotkey(accelerator) {
  if (!UiohookKey) return null;
  const parts = String(accelerator || "")
    .split("+")
    .map((p) => p.trim())
    .filter(Boolean);

  const spec = { ctrl: false, alt: false, shift: false, meta: false, keycode: null, keyName: null };

  for (const raw of parts) {
    const p = raw.toLowerCase();
    if (p === "control" || p === "ctrl") spec.ctrl = true;
    else if (p === "alt") spec.alt = true;
    else if (p === "shift") spec.shift = true;
    else if (p === "super" || p === "meta" || p === "command" || p === "win" || p === "windows") spec.meta = true;
    else spec.keyName = raw;
  }

  if (!spec.keyName) return null;
  const enumKey =
    spec.keyName.length === 1
      ? spec.keyName.toUpperCase()
      : spec.keyName.charAt(0).toUpperCase() + spec.keyName.slice(1);
  const keycode = UiohookKey[enumKey];
  if (!keycode) return null;
  spec.keycode = keycode;
  spec.keyName = enumKey;
  return spec;
}

function setupHoldToTalk() {
  if (!uIOhook || !UiohookKey) return false;
  const spec = parseHoldHotkey(HOTKEY);
  if (!spec) return false;

  debugLog("electron/main.cjs:setupHoldToTalk", "uIOhook enabled", { hotkey: HOTKEY, parsed: spec });

  const matches = (event) => {
    if (event.keycode !== spec.keycode) return false;
    if (spec.ctrl && !event.ctrlKey) return false;
    if (spec.alt && !event.altKey) return false;
    if (spec.shift && !event.shiftKey) return false;
    if (spec.meta && !event.metaKey) return false;
    return true;
  };

  uIOhook.on("keydown", (event) => {
    if (matches(event)) sendStartCommand();
  });

  uIOhook.on("keyup", (event) => {
    const mainKeyReleased = event.keycode === spec.keycode;
    const modifiersReleased =
      (spec.ctrl && !event.ctrlKey) ||
      (spec.alt && !event.altKey) ||
      (spec.shift && !event.shiftKey) ||
      (spec.meta && !event.metaKey);
    if (holdActive && (mainKeyReleased || modifiersReleased)) sendStopCommand();
  });

  uIOhook.start();
  return true;
}

async function pasteIntoFocusedApp(text) {
  // Most reliable cross-app approach: clipboard + Ctrl+V (SendKeys).
  // Note: some elevated apps may block this; in that case, run this app elevated too.
  clipboard.writeText(text);

  await new Promise((r) => setTimeout(r, 50));

  if (sendHelperCommand("paste")) return;

  // Fallback: one-shot spawn if the helper is unavailable.
  await new Promise((resolve) => {
    const ps = spawn(
      "powershell",
      [
        "-NoProfile",
        "-Command",
        "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.SendKeys]::SendWait('^v')"
      ],
      { windowsHide: true }
    );
    ps.on("exit", () => resolve());
    ps.on("error", () => resolve());
  });
}

function createOverlayWindow() {
  // Pre-created and shown/hidden (never recreated) so it appears instantly.
  // focusable:false + showInactive() keep focus on the app being dictated into.
  overlayWin = new BrowserWindow({
    width: OVERLAY_WIDTH,
    height: OVERLAY_HEIGHT,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false,
    show: false,
    hasShadow: false,
    webPreferences: { nodeIntegration: true, contextIsolation: false }
  });
  overlayWin.setIgnoreMouseEvents(true);
  overlayWin.setAlwaysOnTop(true, "screen-saver");
  overlayWin.loadFile(path.join(__dirname, "overlay.html"));
  overlayWin.on("closed", () => {
    overlayWin = null;
  });
}

function overlaySend(channel, payload) {
  if (!overlayWin || overlayWin.isDestroyed()) return;
  try {
    overlayWin.webContents.send(channel, payload);
  } catch {}
}

function positionOverlay() {
  const wa = screen.getPrimaryDisplay().workArea;
  const x = Math.round(wa.x + (wa.width - OVERLAY_WIDTH) / 2);
  const y = Math.round(wa.y + wa.height - OVERLAY_HEIGHT - 24);
  overlayWin.setBounds({ x, y, width: OVERLAY_WIDTH, height: OVERLAY_HEIGHT });
}

function showOverlay(state) {
  if (!overlayEnabled || !overlayWin || overlayWin.isDestroyed()) return;
  overlaySend("state", state);
  if (!overlayWin.isVisible()) {
    positionOverlay();
    overlayWin.showInactive();
  }
}

function hideOverlay() {
  if (overlayWin && !overlayWin.isDestroyed() && overlayWin.isVisible()) {
    overlayWin.hide();
  }
}

function setupTrayIfPossible() {
  if (!fs.existsSync(TRAY_ICON_PATH)) return;

  tray = new Tray(TRAY_ICON_PATH);
  tray.setToolTip("Local Whisper");
  updateTrayMenu();
}

function updateTrayMenu() {
  if (!tray) return;

  const menu = Menu.buildFromTemplate([
    { label: `Hold-to-talk: ${HOTKEY}`, enabled: false },
    { label: `Toggle mode: ${MODE_TOGGLE_HOTKEY}`, enabled: false },
    { type: "separator" },
    { label: "Mode", enabled: false },
    {
      label: "Simple STT (paste transcript)",
      type: "radio",
      checked: currentMode === "stt",
      click: () => setMode("stt")
    },
    {
      label: llmAvailable 
        ? "Voice Command (assistant speaks)" 
        : "Voice Command (LM Studio unavailable)",
      type: "radio",
      checked: currentMode === "assistant",
      enabled: llmAvailable,
      click: () => setMode("assistant")
    },
    { type: "separator" },
    { label: "STT Engine", enabled: false },
    {
      label: "Whisper (faster-whisper)",
      type: "radio",
      checked: currentSttEngine === "whisper",
      click: () => setSttEngine("whisper")
    },
    {
      label: "Moonshine (on-device)",
      type: "radio",
      checked: currentSttEngine === "moonshine",
      click: () => setSttEngine("moonshine")
    },
    { type: "separator" },
    {
      label: "Sound cues",
      type: "checkbox",
      checked: soundCuesEnabled,
      click: (item) => {
        soundCuesEnabled = item.checked;
        saveSettings();
      }
    },
    {
      label: "Waveform overlay",
      type: "checkbox",
      checked: overlayEnabled,
      click: (item) => {
        overlayEnabled = item.checked;
        saveSettings();
        if (!overlayEnabled) hideOverlay();
      }
    },
    { type: "separator" },
    {
      label: llmAvailable
        ? "✓ LM Studio: Available"
        : "✗ LM Studio: Not reachable",
      enabled: false
    },
    { type: "separator" },
    {
      label: "Quit",
      click: () => app.quit()
    }
  ]);

  tray.setContextMenu(menu);
}

app.on("window-all-closed", (e) => {
  // Tray-only app: never quit when windows close.
  e.preventDefault();
});

app.whenReady().then(() => {
  app.setAppUserModelId("local-whisper-app");

  loadSettings();
  debugLog("electron/main.cjs:whenReady", "app ready", { wsUrl: WS_URL, hotkey: HOTKEY, mode: currentMode });

  setupTrayIfPossible();
  createOverlayWindow();
  ensureHelper();

  // Spawn Python immediately so model warm-up starts as early as possible.
  // If another server instance already owns the port, our spawn exits and the
  // websocket connection below attaches to the existing one.
  startPythonService();
  connectWebSocket();

  const toggleOk = globalShortcut.register(MODE_TOGGLE_HOTKEY, () => toggleMode());
  if (!toggleOk) notify("Local Whisper", `Failed to register mode toggle: ${MODE_TOGGLE_HOTKEY}`);

  const holdOk = setupHoldToTalk();
  if (holdOk) {
    notify("Local Whisper", `Hold-to-talk enabled: ${HOTKEY}. Mode: ${modeLabel(currentMode)}.`);
    return;
  }

  // Fallback: press-to-start only (no key-up support).
  const ok = globalShortcut.register(HOTKEY, () => sendStartCommand());
  if (!ok) notify("Local Whisper", `Failed to register hotkey: ${HOTKEY}`);
  else notify("Local Whisper", `Push-to-talk fallback (no key-up): ${HOTKEY}`);
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  try {
    if (uIOhook) uIOhook.stop();
  } catch {}
  try {
    if (ws) ws.close();
  } catch {}
  try {
    if (pythonProc && !pythonProc.killed) pythonProc.kill();
  } catch {}
  try {
    if (helperProc && !helperProc.killed) helperProc.kill();
  } catch {}
});

