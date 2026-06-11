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

    # Keep the input stream open between sessions (instant start, no clipped
    # onsets). Costs a permanently-lit mic indicator; set to 0 to opt out.
    persistent_mic: bool = os.getenv("LOCAL_WHISPER_PERSISTENT_MIC", "1") != "0"
    # Audio captured just before the session starts, prepended to the recording.
    pre_roll_ms: int = int(os.getenv("PRE_ROLL_MS", "250"))

    # Defaults tuned for hold-to-talk dictation:
    # - record up to 2 minutes per hold
    # - allow up to 5s pause before auto-stop
    # - require at least 5s before auto-stop on silence
    max_silence_ms: int = int(os.getenv("MAX_SILENCE_MS", "5000"))
    max_record_ms: int = int(os.getenv("MAX_RECORD_MS", "120000"))
    min_record_ms: int = int(os.getenv("MIN_RECORD_MS", "5000"))

    moonshine_model_dir: Optional[str] = os.getenv("MOONSHINE_MODEL_DIR") or None
    moonshine_model_arch: int = int(os.getenv("MOONSHINE_MODEL_ARCH", "5"))  # 5 = MEDIUM_STREAMING


SETTINGS = Settings()


