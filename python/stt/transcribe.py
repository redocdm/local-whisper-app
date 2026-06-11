from stt.model import get_model, get_moonshine_transcriber
from stt.settings import SETTINGS


def transcribe_pcm(pcm_bytes: bytes) -> str:
    """Transcribe int16 PCM with Whisper, fed directly (no temp WAV round trip)."""
    import numpy as np

    model = get_model()
    audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(
        audio,
        language="en",
        vad_filter=False,
        # Greedy decoding: ~2-3x faster than beam 3 and near-identical for
        # short dictation. Don't condition across segments either.
        beam_size=1,
        condition_on_previous_text=False,
    )
    return "".join(seg.text for seg in segments).strip()


class MoonshineStreamingSession:
    """
    Feeds audio to Moonshine *while recording* (via the recorder's on_frame
    callback), so the transcript is essentially ready at key release instead
    of being computed afterwards. finish() is idempotent.
    """

    def __init__(self) -> None:
        import numpy as np
        from moonshine_voice import TranscriptEventListener

        self._np = np
        self._transcriber = get_moonshine_transcriber()
        self._lines: list[str] = []
        self.finished = False
        outer = self

        class _Collector(TranscriptEventListener):
            def on_line_completed(self, event):  # noqa: ANN001
                t = (event.line.text or "").strip()
                if t:
                    outer._lines.append(t)

        self._collector = _Collector()
        self._transcriber.add_listener(self._collector)
        self._transcriber.start()

    def feed(self, frame: bytes) -> None:
        audio = self._np.frombuffer(frame, dtype=self._np.int16).astype(self._np.float32) / 32768.0
        self._transcriber.add_audio(audio, SETTINGS.sample_rate)

    def finish(self) -> str:
        if self.finished:
            return " ".join(self._lines)
        self.finished = True
        try:
            self._transcriber.stop()
        finally:
            self._transcriber.remove_listener(self._collector)
        return " ".join(self._lines)
