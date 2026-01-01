import os
import subprocess


def speak(text: str) -> None:
    """
    Speak text out loud using Windows SAPI via PowerShell (local, no extra deps).
    """
    enabled = (os.getenv("ASSISTANT_TTS_ENABLED", "1") or "1") != "0"
    if not enabled:
        return

    text = (text or "").strip()
    if not text:
        return

    # Read text from stdin to avoid command injection/escaping issues.
    script = (
        "$t=[Console]::In.ReadToEnd();"
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$s.Speak($t);"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except Exception:
        # Best-effort only; don't crash the WS server.
        return


