import os
from dataclasses import dataclass
from typing import Optional


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


