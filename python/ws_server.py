import asyncio
import json
import os
import sys
import threading
from dataclasses import dataclass

import websockets
from websockets.exceptions import ConnectionClosed

from assistant.agent import SimpleAgent
from assistant.config import CONFIG
from assistant.openai_compat_client import OpenAICompatClient
from stt.recording import record_pcm_until_silence
from stt.settings import SETTINGS
from stt.transcribe import safe_remove_file, transcribe_wav
from stt.wav_utils import write_pcm_to_temp_wav
from tts.sapi_tts import speak
from tools.assistant_tools import build_tools
from util.logging_utils import dbg, log


dbg("H1", "python/ws_server.py:startup", "python module import", {"pid": os.getpid()})


async def send_json(ws, payload: dict) -> None:
    try:
        await ws.send(json.dumps(payload))
    except ConnectionClosed:
        return


@dataclass
class AppState:
    busy: bool = False
    stop_event: "threading.Event | None" = None
    task: "asyncio.Task | None" = None
    mode: str = "stt"  # "stt" | "assistant"
    llm_available: bool = False  # Whether LM Studio/LLM server is reachable


STATE = AppState()
AGENT = SimpleAgent()
_tool_schemas, _tool_fns = build_tools()
AGENT.set_tools(_tool_schemas, _tool_fns)

# Track active WebSocket connections for broadcasting status updates
_active_connections = set()

# Health check client for LM Studio availability
_health_check_client = OpenAICompatClient(
    base_url=CONFIG.llm_base_url,
    model=CONFIG.llm_model,
    temperature=CONFIG.llm_temperature,
)


async def _run_session(ws, stop_event: "threading.Event", mode: str) -> None:
    dbg("H_PTT3", "python/ws_server.py:_run_session", "session started", {"pid": os.getpid(), "mode": mode})
    try:
        await send_json(ws, {"type": "status", "state": "listening"})

        pcm_bytes = await asyncio.to_thread(record_pcm_until_silence, stop_event)

        if len(pcm_bytes) < int(SETTINGS.sample_rate * 0.15) * 2:
            await send_json(ws, {"type": "status", "state": "idle"})
            return

        wav_path = write_pcm_to_temp_wav(pcm_bytes)
        try:
            await send_json(ws, {"type": "status", "state": "transcribing"})
            text = await asyncio.to_thread(transcribe_wav, wav_path)
        finally:
            safe_remove_file(wav_path)

        if mode == "assistant":
            await send_json(ws, {"type": "status", "state": "thinking"})
            response = await asyncio.to_thread(AGENT.run, text)
            await send_json(ws, {"type": "assistant_result", "text": response})
            await send_json(ws, {"type": "status", "state": "speaking"})
            await asyncio.to_thread(speak, response)
        else:
            await send_json(ws, {"type": "result", "text": text})

        await send_json(ws, {"type": "status", "state": "idle"})
    except Exception as e:  # noqa: BLE001
        await send_json(ws, {"type": "error", "message": str(e)})
        await send_json(ws, {"type": "status", "state": "idle"})
    finally:
        STATE.busy = False
        STATE.stop_event = None
        STATE.task = None


async def check_llm_health() -> bool:
    """Check if LM Studio/LLM server is available."""
    try:
        return await asyncio.to_thread(_health_check_client.check_health, timeout=3.0)
    except Exception:
        return False


async def broadcast_llm_status():
    """Broadcast LLM status to all connected clients."""
    if not _active_connections:
        return
    message = {"type": "llm_status", "available": STATE.llm_available}
    disconnected = set()
    for conn in _active_connections:
        try:
            await send_json(conn, message)
        except Exception:
            disconnected.add(conn)
    # Clean up disconnected clients
    _active_connections.difference_update(disconnected)


async def handler(ws):
    # Register this connection
    _active_connections.add(ws)
    
    # Send initial status with LLM availability
    await send_json(ws, {"type": "status", "state": "idle"})
    STATE.llm_available = await check_llm_health()
    await send_json(ws, {"type": "llm_status", "available": STATE.llm_available})
    
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "start":
                dbg("H_PTT3", "python/ws_server.py:handler", "start command received", {"busy": STATE.busy})
                if STATE.busy:
                    await send_json(ws, {"type": "error", "message": "Already listening/transcribing."})
                    continue

                mode = msg.get("mode") or "stt"
                if mode not in ("stt", "assistant"):
                    mode = "stt"
                
                # If assistant mode requested but LLM unavailable, fall back to STT
                if mode == "assistant" and not STATE.llm_available:
                    await send_json(ws, {
                        "type": "error",
                        "message": "LM Studio server not reachable. Assistant mode unavailable. Using STT mode."
                    })
                    mode = "stt"
                
                STATE.mode = mode

                STATE.busy = True
                STATE.stop_event = threading.Event()
                STATE.task = asyncio.create_task(_run_session(ws, STATE.stop_event, mode))
            
            elif msg.get("type") == "check_llm":
                # Allow Electron to request LLM health check
                STATE.llm_available = await check_llm_health()
                await send_json(ws, {"type": "llm_status", "available": STATE.llm_available})

            elif msg.get("type") == "stop":
                dbg(
                    "H_PTT_STOP2",
                    "python/ws_server.py:handler",
                    "stop command received",
                    {"busy": STATE.busy, "hasStopEvent": STATE.stop_event is not None},
                )
                if STATE.stop_event is not None:
                    STATE.stop_event.set()

            elif msg.get("type") == "ping":
                await send_json(ws, {"type": "pong"})
    finally:
        # Unregister connection when it closes
        _active_connections.discard(ws)


async def periodic_health_check():
    """Periodically check LLM availability (every 30 seconds) and broadcast changes."""
    while True:
        await asyncio.sleep(30)
        previous_status = STATE.llm_available
        STATE.llm_available = await check_llm_health()
        
        # Only log and broadcast if status changed
        if STATE.llm_available != previous_status:
            if STATE.llm_available:
                log("LM Studio server is available.")
            else:
                log("LM Studio server is not reachable. Assistant mode disabled.")
            # Broadcast status change to all connected clients
            await broadcast_llm_status()


async def main() -> None:
    log(f"Starting websocket server on ws://{SETTINGS.host}:{SETTINGS.port}")
    
    # Initial LLM health check
    STATE.llm_available = await check_llm_health()
    if STATE.llm_available:
        log(f"LM Studio server is available at {CONFIG.llm_base_url}")
    else:
        log(f"LM Studio server not reachable at {CONFIG.llm_base_url}. Assistant mode will be disabled.")
    
    dbg("H3", "python/ws_server.py:main", "serve starting", {"host": SETTINGS.host, "port": SETTINGS.port, "pid": os.getpid()})
    try:
        async with websockets.serve(
            handler,
            SETTINGS.host,
            SETTINGS.port,
            max_size=4_000_000,
            ping_interval=30,
            ping_timeout=120,
        ):
            dbg("H3", "python/ws_server.py:main", "serve started", {"host": SETTINGS.host, "port": SETTINGS.port})
            # Start periodic health check in background
            asyncio.create_task(periodic_health_check())
            await asyncio.Future()
    except OSError as e:
        dbg(
            "H1",
            "python/ws_server.py:main",
            "serve bind failed",
            {"errno": getattr(e, "errno", None), "winerror": getattr(e, "winerror", None), "str": str(e)},
        )
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Shutting down.")
        sys.exit(0)


