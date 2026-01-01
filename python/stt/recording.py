import queue
import threading

import sounddevice as sd
import webrtcvad

from stt.settings import SETTINGS
from util.logging_utils import dbg


def record_pcm_until_silence(stop_event: "threading.Event | None" = None) -> bytes:
    """
    Record 16kHz mono int16 PCM until silence thresholds are met, or stop_event is set.
    Returns raw PCM bytes.
    """
    if SETTINGS.frame_ms not in (10, 20, 30):
        raise ValueError("VAD_FRAME_MS must be one of 10, 20, 30")

    vad = webrtcvad.Vad(int(max(0, min(3, SETTINGS.vad_aggressiveness))))
    frame_samples = int(SETTINGS.sample_rate * SETTINGS.frame_ms / 1000)

    q: "queue.Queue[bytes]" = queue.Queue(maxsize=256)

    def callback(indata, frames, time, status):  # noqa: ANN001
        if status:
            pass
        pcm = indata.reshape(-1).tobytes()
        try:
            q.put_nowait(pcm)
        except queue.Full:
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
        dbg(
            "H_PTT2",
            "python/stt/recording.py:record_pcm_until_silence",
            "record loop start",
            {
                "frame_ms": SETTINGS.frame_ms,
                "max_silence_ms": SETTINGS.max_silence_ms,
                "max_record_ms": SETTINGS.max_record_ms,
                "min_record_ms": SETTINGS.min_record_ms,
            },
        )
        while True:
            frame = q.get()
            if len(frame) < frame_samples * 2:
                continue

            is_speech = vad.is_speech(frame, SETTINGS.sample_rate)
            total_ms += SETTINGS.frame_ms

            if stop_event is not None and stop_event.is_set():
                dbg(
                    "H_PTT2",
                    "python/stt/recording.py:record_pcm_until_silence",
                    "stop reason: stop",
                    {"total_ms": total_ms, "silence_ms": silence_ms, "started": started},
                )
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
                dbg(
                    "H_PTT2",
                    "python/stt/recording.py:record_pcm_until_silence",
                    "stop reason: max_record_ms",
                    {"total_ms": total_ms, "silence_ms": silence_ms, "started": started},
                )
                break
            if silence_ms >= SETTINGS.max_silence_ms and total_ms >= SETTINGS.min_record_ms:
                dbg(
                    "H_PTT2",
                    "python/stt/recording.py:record_pcm_until_silence",
                    "stop reason: silence",
                    {"total_ms": total_ms, "silence_ms": silence_ms, "started": started},
                )
                break

    return bytes(captured)


