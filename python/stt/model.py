from __future__ import annotations

import threading
from typing import Any, Optional

from stt.cudnn import setup_cudnn_dll_path
from stt.settings import SETTINGS
from util.logging_utils import dbg, log


# faster_whisper is imported lazily inside get_model(): the import itself is
# heavy and would otherwise delay the websocket server bind at startup.
_MODEL: Optional[Any] = None
_MODEL_LOCK = threading.Lock()
_MOONSHINE_TRANSCRIBER: Optional[Any] = None


def get_model() -> Any:
    global _MODEL  # noqa: PLW0603
    if _MODEL is not None:
        return _MODEL

    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        return _load_model_locked()


def _load_model_locked() -> Any:
    global _MODEL  # noqa: PLW0603

    from faster_whisper import WhisperModel

    setup_cudnn_dll_path()

    compute_candidates = [SETTINGS.compute_type]
    for fallback in ("float16", "int8", "float32"):
        if fallback not in compute_candidates:
            compute_candidates.append(fallback)

    last_error: Exception | None = None
    for compute_type in compute_candidates:
        try:
            dbg(
                "H2",
                "python/stt/model.py:get_model",
                "attempt load_model",
                {"model": SETTINGS.model, "device": SETTINGS.device, "compute_type": compute_type},
            )
            log(
                "Loading model "
                f"(model={SETTINGS.model}, device={SETTINGS.device}, compute_type={compute_type})"
            )
            _MODEL = WhisperModel(
                SETTINGS.model,
                device=SETTINGS.device,
                compute_type=compute_type,
                download_root=SETTINGS.download_root,
            )
            SETTINGS.compute_type = compute_type
            dbg(
                "H2",
                "python/stt/model.py:get_model",
                "load_model success",
                {"selected_compute_type": compute_type},
            )
            return _MODEL
        except ValueError as e:
            last_error = e
            log(f"Model load failed for compute_type={compute_type}: {e}")

    raise RuntimeError(f"Failed to load Whisper model: {last_error}") from last_error


def get_moonshine_transcriber() -> Any:
    global _MOONSHINE_TRANSCRIBER  # noqa: PLW0603
    if _MOONSHINE_TRANSCRIBER is not None:
        return _MOONSHINE_TRANSCRIBER

    from pathlib import Path

    from moonshine_voice import Transcriber
    from moonshine_voice.download_file import get_cache_dir
    from moonshine_voice.moonshine_api import ModelArch

    model_path = SETTINGS.moonshine_model_dir
    if not model_path:
        cache = get_cache_dir("moonshine_voice")
        model_path = str(
            Path(cache) / "download.moonshine.ai" / "model" / "medium-streaming-en" / "quantized"
        )

    model_arch = ModelArch(SETTINGS.moonshine_model_arch)
    log(f"Loading Moonshine model (path={model_path}, arch={model_arch.name})")
    _MOONSHINE_TRANSCRIBER = Transcriber(model_path=model_path, model_arch=model_arch)
    return _MOONSHINE_TRANSCRIBER


def warm_up_whisper() -> None:
    """
    Load the Whisper model and run a tiny inference so the first real
    transcription doesn't pay model load + CUDA init costs.
    """
    try:
        import numpy as np

        model = get_model()
        silence = np.zeros(SETTINGS.sample_rate // 2, dtype=np.float32)
        segments, _info = model.transcribe(silence, language="en", beam_size=1, vad_filter=False)
        for _ in segments:
            pass
        log("Whisper warm-up complete.")
    except Exception as e:  # noqa: BLE001
        log(f"Whisper warm-up failed: {e}")


def warm_up_moonshine() -> None:
    """Pre-load the Moonshine transcriber (no-op if the package is missing)."""
    try:
        get_moonshine_transcriber()
        log("Moonshine warm-up complete.")
    except Exception as e:  # noqa: BLE001
        log(f"Moonshine warm-up skipped: {e}")


