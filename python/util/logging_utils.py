import json
import os
from datetime import datetime


def log(message: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


_DBG_LOG_PATH = os.getenv("LOCAL_WHISPER_DEBUG_LOG_PATH") or r"f:\Projects\AppDev\.cursor\debug.log"


def dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    """
    Best-effort debug logger. Writes JSONL to a local file.

    Intended for development only; failures are swallowed.
    """
    try:
        payload = {
            "sessionId": "debug-session",
            "runId": os.getenv("LOCAL_WHISPER_RUN_ID", "run"),
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        with open(_DBG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass


