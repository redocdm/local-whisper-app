import os
import tempfile
import wave

from stt.settings import SETTINGS


def write_pcm_to_temp_wav(pcm_bytes: bytes) -> str:
    fd, wav_path = tempfile.mkstemp(prefix="localwhisper_", suffix=".wav")
    os.close(fd)

    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(SETTINGS.sample_rate)
        wf.writeframes(pcm_bytes)

    return wav_path


