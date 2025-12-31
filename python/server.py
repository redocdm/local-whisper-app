import asyncio
import json
import os
import queue
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad
import websockets
from websockets.exceptions import ConnectionClosed
from faster_whisper import WhisperModel


def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


# region agent log (debug mode)
_DBG_LOG_PATH = r"f:\Projects\AppDev\.cursor\debug.log"


def _dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        payload = {
            "sessionId": "debug-session",
            "runId": os.getenv("LOCAL_WHISPER_RUN_ID", "run"),
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        with open(_DBG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass


_dbg("H1", "python/server.py:startup", "python module import", {"pid": os.getpid()})
# endregion


@dataclass
class Settings:
    host: str = os.getenv("LOCAL_WHISPER_HOST", "127.0.0.1")
    port: int = int(os.getenv("LOCAL_WHISPER_PORT", "8765"))

    model: str = os.getenv("WHISPER_MODEL", "small.en")
    device: str = os.getenv("WHISPER_DEVICE", "cuda")
    compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    download_root: Optional[str] = os.getenv("WHISPER_MODEL_DIR") or None

    sample_rate: int = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))
    vad_aggressiveness: int = int(os.getenv("VAD_AGGRESSIVENESS", "2"))  # 0..3
    frame_ms: int = int(os.getenv("VAD_FRAME_MS", "30"))  # 10/20/30

    # Defaults tuned for "command" usage:
    # - record up to 30s
    # - allow up to 5s pause before auto-stop
    # - require at least 5s before auto-stop on silence
    max_silence_ms: int = int(os.getenv("MAX_SILENCE_MS", "5000"))
    max_record_ms: int = int(os.getenv("MAX_RECORD_MS", "30000"))
    min_record_ms: int = int(os.getenv("MIN_RECORD_MS", "5000"))


SETTINGS = Settings()


def _setup_cudnn_dll_path() -> None:
    """Add cuDNN DLL directory to Windows DLL search path before model initialization."""
    if sys.platform != "win32":
        return

    # IMPORTANT: keep add_dll_directory handles alive for the life of the process.
    # If the returned handle is GC'd, Windows removes that directory from the search path.
    global _DLL_DIR_HANDLES  # noqa: PLW0603
    try:
        _DLL_DIR_HANDLES
    except NameError:
        _DLL_DIR_HANDLES = []  # type: ignore[assignment]

    # Project-local NVIDIA runtime bundle (recommended for older GPUs / repeatable installs).
    vendor_cuda11_dir = os.path.join(os.path.dirname(__file__), "vendor", "nvidia", "cuda11")

    # Common locations for NVIDIA/cuDNN DLLs (less repeatable; kept as fallbacks).
    cudnn_paths = [
        vendor_cuda11_dir,
        os.getenv("CUDNN_PATH", ""),  # Custom environment variable
        os.path.join(os.getenv("CUDA_PATH", ""), "bin"),  # CUDA Toolkit bin
        os.path.join(os.getenv("CUDA_PATH", ""), "lib"),  # CUDA Toolkit lib
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve",  # Not recommended; may be incomplete/mismatched
    ]

    # Also check if nvidia-cudnn pip package is installed
    try:
        import site

        for site_pkg in site.getsitepackages() + [site.getusersitepackages()]:
            nvidia_cudnn_path = os.path.join(site_pkg, "nvidia", "cudnn", "bin")
            if os.path.exists(nvidia_cudnn_path):
                cudnn_paths.insert(0, nvidia_cudnn_path)  # Prefer pip-installed version
    except Exception:
        pass

    # Add first directory that looks like it contains NVIDIA runtime DLLs we need.
    for path in cudnn_paths:
        if not path or not os.path.exists(path):
            continue

        marker_dlls = [
            # cuDNN 9 (CUDA 12)
            "cudnn_ops64_9.dll",
            # cuDNN 8 (CUDA 11)
            "cudnn_ops_infer64_8.dll",
            "cudnn_cnn_infer64_8.dll",
            # cuBLAS
            "cublas64_11.dll",
        ]
        if any(os.path.exists(os.path.join(path, dll)) for dll in marker_dlls):
            try:
                _DLL_DIR_HANDLES.append(os.add_dll_directory(path))  # type: ignore[attr-defined]
                log(f"Added cuDNN DLL directory: {path}")
                return
            except Exception as e:
                log(f"Failed to add DLL directory {path}: {e}")

    # If not found, log a warning but don't fail (CPU fallback may work)
    log("Warning: cuDNN DLL not found. GPU operations may fail. Install cuDNN or set CUDNN_PATH.")


# Setup cuDNN DLL path before loading model
_setup_cudnn_dll_path()


def load_model() -> WhisperModel:
    compute_candidates = [SETTINGS.compute_type]
    for fallback in ("float16", "int8", "float32"):
        if fallback not in compute_candidates:
            compute_candidates.append(fallback)

    last_error: Exception | None = None
    for compute_type in compute_candidates:
        try:
            # region agent log H2
            _dbg(
                "H2",
                "python/server.py:load_model",
                "attempt load_model",
                {"model": SETTINGS.model, "device": SETTINGS.device, "compute_type": compute_type},
            )
            # endregion
            log(
                "Loading model "
                f"(model={SETTINGS.model}, device={SETTINGS.device}, compute_type={compute_type})"
            )
            model = WhisperModel(
                SETTINGS.model,
                device=SETTINGS.device,
                compute_type=compute_type,
                download_root=SETTINGS.download_root,
            )
            SETTINGS.compute_type = compute_type
            # region agent log H2
            _dbg(
                "H2",
                "python/server.py:load_model",
                "load_model success",
                {"selected_compute_type": compute_type},
            )
            # endregion
            return model
        except ValueError as e:
            last_error = e
            log(f"Model load failed for compute_type={compute_type}: {e}")

    raise RuntimeError(f"Failed to load Whisper model: {last_error}") from last_error


MODEL: WhisperModel = load_model()


def _record_until_silence(stop_event: "threading.Event | None" = None) -> bytes:
    if SETTINGS.frame_ms not in (10, 20, 30):
        raise ValueError("VAD_FRAME_MS must be one of 10, 20, 30")

    vad = webrtcvad.Vad(int(max(0, min(3, SETTINGS.vad_aggressiveness))))
    frame_samples = int(SETTINGS.sample_rate * SETTINGS.frame_ms / 1000)

    q: "queue.Queue[bytes]" = queue.Queue(maxsize=256)

    def callback(indata, frames, time, status):  # noqa: ANN001
        if status:
            # Don't spam; status happens often on some devices.
            pass
        # indata is int16 mono: shape (frames, 1)
        pcm = indata.reshape(-1).tobytes()
        try:
            q.put_nowait(pcm)
        except queue.Full:
            # If the consumer lags behind, drop audio to keep latency bounded.
            pass

    started = False
    captured = bytearray()
    silence_ms = 0
    total_ms = 0

    with sd.InputStream(
        samplerate=SETTINGS.sample_rate,
        channels=1,
        dtype="int16",
        blocksize=frame_samples,
        callback=callback,
    ):
        # region agent log H_PTT2
        _dbg("H_PTT2", "python/server.py:_record_until_silence", "record loop start", {"frame_ms": SETTINGS.frame_ms, "max_silence_ms": SETTINGS.max_silence_ms, "max_record_ms": SETTINGS.max_record_ms, "min_record_ms": SETTINGS.min_record_ms})
        # endregion
        while True:
            frame = q.get()
            if len(frame) < frame_samples * 2:
                continue

            is_speech = vad.is_speech(frame, SETTINGS.sample_rate)
            total_ms += SETTINGS.frame_ms

            if stop_event is not None and stop_event.is_set():
                # region agent log H_PTT2
                _dbg(
                    "H_PTT2",
                    "python/server.py:_record_until_silence",
                    "stop reason: stop",
                    {"total_ms": total_ms, "silence_ms": silence_ms, "started": started},
                )
                # endregion
                break

            if not started:
                if is_speech:
                    started = True
                    captured.extend(frame)
                if total_ms >= SETTINGS.max_record_ms:
                    break
                continue

            captured.extend(frame)

            if is_speech:
                silence_ms = 0
            else:
                silence_ms += SETTINGS.frame_ms

            if total_ms >= SETTINGS.max_record_ms:
                # region agent log H_PTT2
                _dbg("H_PTT2", "python/server.py:_record_until_silence", "stop reason: max_record_ms", {"total_ms": total_ms, "silence_ms": silence_ms, "started": started})
                # endregion
                break
            if silence_ms >= SETTINGS.max_silence_ms and total_ms >= SETTINGS.min_record_ms:
                # region agent log H_PTT2
                _dbg("H_PTT2", "python/server.py:_record_until_silence", "stop reason: silence", {"total_ms": total_ms, "silence_ms": silence_ms, "started": started})
                # endregion
                break

    return bytes(captured)


def record_wav_to_tempfile(pcm_bytes: bytes) -> str:
    fd, wav_path = tempfile.mkstemp(prefix="localwhisper_", suffix=".wav")
    os.close(fd)

    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SETTINGS.sample_rate)
        wf.writeframes(pcm_bytes)

    return wav_path


def transcribe_wav(wav_path: str) -> str:
    segments, _info = MODEL.transcribe(
        wav_path,
        language="en",
        # We already VAD-stop during recording; avoid double-VAD here.
        vad_filter=False,
        # For commands, smaller beam is snappier.
        beam_size=3,
    )
    return "".join(seg.text for seg in segments).strip()


async def send_json(ws, payload: dict) -> None:
    try:
        await ws.send(json.dumps(payload))
    except ConnectionClosed:
        # Client disconnected (often due to keepalive timeout). Ignore.
        return


@dataclass
class AppState:
    busy: bool = False
    stop_event: "threading.Event | None" = None
    task: "asyncio.Task | None" = None


STATE = AppState()


async def _run_session(ws, stop_event: "threading.Event") -> None:
    # region agent log H_PTT3
    _dbg("H_PTT3", "python/server.py:_run_session", "session started", {"pid": os.getpid()})
    # endregion
    try:
        await send_json(ws, {"type": "status", "state": "listening"})

        # Recording must happen in a worker thread because sounddevice blocks.
        pcm_bytes = await asyncio.to_thread(_record_until_silence, stop_event)

        if len(pcm_bytes) < int(SETTINGS.sample_rate * 0.15) * 2:
            await send_json(ws, {"type": "status", "state": "idle"})
            return

        wav_path = record_wav_to_tempfile(pcm_bytes)
        try:
            await send_json(ws, {"type": "status", "state": "transcribing"})
            text = await asyncio.to_thread(transcribe_wav, wav_path)
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

        await send_json(ws, {"type": "result", "text": text})
        await send_json(ws, {"type": "status", "state": "idle"})
    except Exception as e:  # noqa: BLE001
        await send_json(ws, {"type": "error", "message": str(e)})
        await send_json(ws, {"type": "status", "state": "idle"})
    finally:
        STATE.busy = False
        STATE.stop_event = None
        STATE.task = None


async def handler(ws):
    await send_json(ws, {"type": "status", "state": "idle"})
    async for raw in ws:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if msg.get("type") == "start":
            # region agent log H_PTT3
            _dbg("H_PTT3", "python/server.py:handler", "start command received", {"busy": STATE.busy})
            # endregion
            if STATE.busy:
                await send_json(ws, {"type": "error", "message": "Already listening/transcribing."})
                continue

            STATE.busy = True
            STATE.stop_event = threading.Event()
            STATE.task = asyncio.create_task(_run_session(ws, STATE.stop_event))
        elif msg.get("type") == "stop":
            # region agent log H_PTT_STOP2
            _dbg("H_PTT_STOP2", "python/server.py:handler", "stop command received", {"busy": STATE.busy, "hasStopEvent": STATE.stop_event is not None})
            # endregion
            if STATE.stop_event is not None:
                STATE.stop_event.set()
        elif msg.get("type") == "ping":
            await send_json(ws, {"type": "pong"})


async def main() -> None:
    log(f"Starting websocket server on ws://{SETTINGS.host}:{SETTINGS.port}")
    # region agent log H3
    _dbg("H3", "python/server.py:main", "serve starting", {"host": SETTINGS.host, "port": SETTINGS.port, "pid": os.getpid()})
    # endregion
    try:
        async with websockets.serve(
            handler,
            SETTINGS.host,
            SETTINGS.port,
            max_size=4_000_000,
            ping_interval=30,
            ping_timeout=120,
        ):
            # region agent log H3
            _dbg("H3", "python/server.py:main", "serve started", {"host": SETTINGS.host, "port": SETTINGS.port})
            # endregion
            await asyncio.Future()
    except OSError as e:
        # region agent log H1
        _dbg("H1", "python/server.py:main", "serve bind failed", {"errno": getattr(e, "errno", None), "winerror": getattr(e, "winerror", None), "str": str(e)})
        # endregion
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Shutting down.")
        sys.exit(0)

