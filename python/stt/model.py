from __future__ import annotations

from typing import Optional

from faster_whisper import WhisperModel

from stt.cudnn import setup_cudnn_dll_path
from stt.settings import SETTINGS
from util.logging_utils import dbg, log


_MODEL: Optional[WhisperModel] = None


def get_model() -> WhisperModel:
    global _MODEL  # noqa: PLW0603
    if _MODEL is not None:
        return _MODEL

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


