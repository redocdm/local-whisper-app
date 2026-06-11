import collections
import contextlib
import queue
import threading
from typing import Callable, Optional

import sounddevice as sd
import webrtcvad

from stt.settings import SETTINGS
from util.logging_utils import dbg, log


def _frame_samples() -> int:
    return int(SETTINGS.sample_rate * SETTINGS.frame_ms / 1000)


class _PersistentMic:
    """
    Keeps one always-open input stream so sessions start instantly (no
    ~100-300ms WASAPI stream-open cost) and a short pre-roll ring buffer
    catches speech that began just before the session started.
    """

    def __init__(self) -> None:
        self._stream: "sd.InputStream | None" = None
        self._lock = threading.Lock()
        self._ring: "collections.deque[bytes]" = collections.deque(
            maxlen=max(1, SETTINGS.pre_roll_ms // SETTINGS.frame_ms)
        )
        self._session_q: "queue.Queue[bytes] | None" = None

    def ensure_started(self) -> bool:
        with self._lock:
            if self._stream is not None:
                return True
            try:
                stream = sd.InputStream(
                    samplerate=SETTINGS.sample_rate,
                    channels=1,
                    dtype="int16",
                    blocksize=_frame_samples(),
                    callback=self._callback,
                )
                stream.start()
                self._stream = stream
                log("Persistent mic stream started.")
                return True
            except Exception as e:  # noqa: BLE001
                log(f"Persistent mic unavailable, using per-session stream: {e}")
                self._stream = None
                return False

    def _callback(self, indata, frames, time, status):  # noqa: ANN001
        pcm = indata.reshape(-1).tobytes()
        with self._lock:
            self._ring.append(pcm)
            q = self._session_q
        if q is not None:
            try:
                q.put_nowait(pcm)
            except queue.Full:
                pass

    def start_session(self) -> "queue.Queue[bytes]":
        q: "queue.Queue[bytes]" = queue.Queue(maxsize=1024)
        with self._lock:
            # Seed with pre-roll so speech that started just before the
            # session message arrived isn't clipped.
            for frame in self._ring:
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    break
            self._session_q = q
        return q

    def end_session(self) -> None:
        with self._lock:
            self._session_q = None


_MIC = _PersistentMic()


def warm_up_mic() -> None:
    if SETTINGS.persistent_mic:
        _MIC.ensure_started()


def record_pcm_until_silence(
    stop_event: "threading.Event | None" = None,
    on_frame: Optional[Callable[[bytes], None]] = None,
) -> bytes:
    """
    Record 16kHz mono int16 PCM until silence thresholds are met, or stop_event
    is set. Captures everything from session start (no VAD gating of the onset);
    returns b"" if no speech was detected at all. `on_frame` is invoked with
    each captured frame, enabling streaming transcription during recording.
    """
    if SETTINGS.frame_ms not in (10, 20, 30):
        raise ValueError("VAD_FRAME_MS must be one of 10, 20, 30")

    vad = webrtcvad.Vad(int(max(0, min(3, SETTINGS.vad_aggressiveness))))
    frame_bytes = _frame_samples() * 2

    use_persistent = SETTINGS.persistent_mic and _MIC.ensure_started()
    if use_persistent:
        q = _MIC.start_session()
        stream_cm = contextlib.nullcontext()
    else:
        q = queue.Queue(maxsize=256)

        def callback(indata, frames, time, status):  # noqa: ANN001
            try:
                q.put_nowait(indata.reshape(-1).tobytes())
            except queue.Full:
                pass

        stream_cm = sd.InputStream(
            samplerate=SETTINGS.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=_frame_samples(),
            callback=callback,
        )

    captured = bytearray()
    any_speech = False
    silence_ms = 0
    total_ms = 0
    stop_reason = "unknown"

    try:
        with stream_cm:
            dbg(
                "H_PTT2",
                "python/stt/recording.py:record_pcm_until_silence",
                "record loop start",
                {
                    "persistent": use_persistent,
                    "frame_ms": SETTINGS.frame_ms,
                    "max_silence_ms": SETTINGS.max_silence_ms,
                    "max_record_ms": SETTINGS.max_record_ms,
                    "min_record_ms": SETTINGS.min_record_ms,
                },
            )
            while True:
                try:
                    frame = q.get(timeout=2)
                except queue.Empty:
                    # Stream stalled (e.g. device lost); don't hang the session.
                    stop_reason = "stream_stalled"
                    break
                if len(frame) < frame_bytes:
                    continue

                if stop_event is not None and stop_event.is_set():
                    stop_reason = "stop"
                    break

                total_ms += SETTINGS.frame_ms
                captured.extend(frame)
                if on_frame is not None:
                    on_frame(frame)

                if vad.is_speech(frame, SETTINGS.sample_rate):
                    any_speech = True
                    silence_ms = 0
                else:
                    silence_ms += SETTINGS.frame_ms

                if total_ms >= SETTINGS.max_record_ms:
                    stop_reason = "max_record_ms"
                    break
                if silence_ms >= SETTINGS.max_silence_ms and total_ms >= SETTINGS.min_record_ms:
                    stop_reason = "silence"
                    break
    finally:
        if use_persistent:
            _MIC.end_session()

    dbg(
        "H_PTT2",
        "python/stt/recording.py:record_pcm_until_silence",
        f"stop reason: {stop_reason}",
        {"total_ms": total_ms, "silence_ms": silence_ms, "any_speech": any_speech},
    )

    return bytes(captured) if any_speech else b""
