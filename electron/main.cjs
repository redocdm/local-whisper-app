const { app, globalShortcut, Notification, clipboard, Menu, Tray } = require("electron");
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
const TRAY_ICON_PATH =
  process.env.LOCAL_WHISPER_TRAY_ICON ||
  path.resolve(__dirname, "..", "logo.png");

let tray = null;
let ws = null;
let wsConnected = false;
let pythonProc = null;
let reconnectTimer = null;
let holdActive = false;
let wasActive = false;

function notify(title, body) {
  try {
    new Notification({ title, body }).show();
  } catch {
    // Notifications can fail in some environments; ignore.
  }
}

function playCue(kind) {
  const enabled = (process.env.LOCAL_WHISPER_SOUND || "1") !== "0";
  if (!enabled) return;

  const cmd = kind === "start" ? "[console]::beep(880,60)" : "[console]::beep(660,60)";
  try {
    // #region agent log H_PTT_SND
    fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:playCue',message:'play cue',data:{kind},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'ptt-run',hypothesisId:'H_PTT_SND'})}).catch(()=>{});
    // #endregion
    const ps = spawn("powershell", ["-NoProfile", "-Command", cmd], { windowsHide: true, stdio: "ignore" });
    ps.on("error", () => {});
  } catch {}
}

function startPythonService() {
  // #region agent log H2
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:startPythonService',message:'startPythonService called',data:{wsUrl:WS_URL,hasPythonProc:!!pythonProc,pythonProcKilled:pythonProc?!!pythonProc.killed:null},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H2'})}).catch(()=>{});
  // #endregion
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

  // #region agent log H2
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:startPythonService',message:'python spawned',data:{pid:pythonProc.pid,pythonExe,pythonArgs},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H2'})}).catch(()=>{});
  // #endregion

  pythonProc.stdout.on("data", (d) => process.stdout.write(`[py] ${d}`));
  pythonProc.stderr.on("data", (d) => process.stderr.write(`[py] ${d}`));
  pythonProc.on("exit", (code) => {
    // #region agent log H4
    fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:pythonExit',message:'python exited',data:{code,hadWsConnected:wsConnected,wsUrl:WS_URL},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H4'})}).catch(()=>{});
    // #endregion
    pythonProc = null;
    wsConnected = false;
    notify("Local Whisper", `STT service stopped (code ${code}).`);
    scheduleReconnect();
  });
}

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  // #region agent log H1
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:connectWebSocket',message:'connectWebSocket attempt',data:{wsUrl:WS_URL,hasPythonProc:!!pythonProc},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H1'})}).catch(()=>{});
  // #endregion

  wsConnected = false;
  ws = new WebSocket(WS_URL);

  ws.on("open", () => {
    // #region agent log H1
    fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:wsOpen',message:'websocket open',data:{wsUrl:WS_URL},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H1'})}).catch(()=>{});
    // #endregion
    wsConnected = true;
    notify("Local Whisper", "Ready (Ctrl+Alt+W to talk).");
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
        notify("Local Whisper", "Listening…");
      }
      if (msg.state === "transcribing") {
        wasActive = true;
        notify("Local Whisper", "Transcribing…");
      }
      if (msg.state === "idle") {
        if (wasActive) playCue("end");
        wasActive = false;
        notify("Local Whisper", "Ready.");
      }
      return;
    }

    if (msg.type === "result" && typeof msg.text === "string") {
      const text = msg.text.trim();
      if (!text) return;

      await pasteIntoFocusedApp(text);
      notify("Local Whisper", "Pasted transcript.");
      return;
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
    // #region agent log H3
    fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:wsError',message:'websocket error',data:{wsUrl:WS_URL,errorMessage:err?err.message:null,errorCode:err?err.code:null},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H3'})}).catch(()=>{});
    // #endregion
    wsConnected = false;
    scheduleReconnect();
  });
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  // #region agent log H2
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:scheduleReconnect',message:'scheduleReconnect set',data:{wsUrl:WS_URL,hasPythonProc:!!pythonProc},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H2'})}).catch(()=>{});
  // #endregion
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    startPythonService();
    connectWebSocket();
  }, 1200);
}

function sendStartCommand() {
  // #region agent log H_PTT1
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:sendStartCommand',message:'hotkey triggered -> send start',data:{wsConnected,wsReadyState:ws?ws.readyState:null,hotkey:HOTKEY},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'ptt-run',hypothesisId:'H_PTT1'})}).catch(()=>{});
  // #endregion
  if (holdActive) return;
  holdActive = true;
  if (!wsConnected || !ws || ws.readyState !== WebSocket.OPEN) {
    notify("Local Whisper", "Not connected yet—starting service…");
    scheduleReconnect();
    return;
  }
  playCue("start");
  ws.send(JSON.stringify({ type: "start" }));
}

function sendStopCommand() {
  if (!holdActive) return;
  holdActive = false;
  // #region agent log H_PTT_STOP1
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:sendStopCommand',message:'hotkey released -> send stop',data:{wsConnected,wsReadyState:ws?ws.readyState:null,hotkey:HOTKEY},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'ptt-run',hypothesisId:'H_PTT_STOP1'})}).catch(()=>{});
  // #endregion
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

  // #region agent log H_PTT_HOOK
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:setupHoldToTalk',message:'uIOhook enabled',data:{hotkey:HOTKEY,parsed:spec},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'ptt-run',hypothesisId:'H_PTT_HOOK'})}).catch(()=>{});
  // #endregion

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
  clipboard.writeText(text);

  await new Promise((r) => setTimeout(r, 50));

  // SendKeys often works well for "paste into focused app" without native Node modules.
  // Note: some elevated apps may block this; in that case, run this app elevated too.
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

function setupTrayIfPossible() {
  if (!fs.existsSync(TRAY_ICON_PATH)) return;

  tray = new Tray(TRAY_ICON_PATH);
  tray.setToolTip("Local Whisper (Hold-to-talk)");

  const menu = Menu.buildFromTemplate([
    { label: "Push-to-talk (Ctrl+Alt+W)", enabled: false },
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

  // #region agent log H1
  fetch('http://127.0.0.1:7242/ingest/a093ff22-0c3c-4383-aa9a-fca72615f301',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'electron/main.cjs:whenReady',message:'app ready',data:{wsUrl:WS_URL,hotkey:HOTKEY},timestamp:Date.now(),sessionId:'debug-session',runId:process.env.LOCAL_WHISPER_RUN_ID||'run',hypothesisId:'H1'})}).catch(()=>{});
  // #endregion

  setupTrayIfPossible();

  // Try connecting first; only spawn Python if no server is listening
  // (scheduleReconnect will spawn Python on connection failure)
  connectWebSocket();

  const holdOk = setupHoldToTalk();
  if (holdOk) {
    notify("Local Whisper", `Hold-to-talk enabled: ${HOTKEY}`);
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
});

