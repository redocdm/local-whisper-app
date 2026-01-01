import os

from stt.model import get_model


def transcribe_wav(wav_path: str) -> str:
    model = get_model()
    segments, _info = model.transcribe(
        wav_path,
        language="en",
        vad_filter=False,
        beam_size=3,
    )
    return "".join(seg.text for seg in segments).strip()


def safe_remove_file(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


