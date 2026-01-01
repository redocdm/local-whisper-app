from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ChatResult:
    content: str
    tool_calls: List[Dict[str, Any]]


class OpenAICompatClient:
    """
    Minimal OpenAI-compatible /v1/chat/completions client using stdlib only.

    Works with LM Studio's local server.
    """

    def __init__(self, base_url: str, model: str, temperature: float = 0.7) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature

    def check_health(self, timeout: float = 3.0) -> bool:
        """
        Check if the LLM server is reachable and responding.
        Returns True if healthy, False otherwise.
        """
        try:
            # Try a minimal health check - just check if the endpoint responds
            url = f"{self._base_url}/models"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                # If we get any response, server is up
                return resp.status == 200
        except Exception:
            return False

    def chat_completion(
        self,
        *,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> ChatResult:
        url = f"{self._base_url}/chat/completions"

        messages_with_system = messages
        if system:
            messages_with_system = [{"role": "system", "content": system}, *messages]

        body: Dict[str, Any] = {
            "model": self._model,
            "messages": messages_with_system,
            "temperature": self._temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = tools

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace") if getattr(e, "fp", None) else str(e)
            raise RuntimeError(f"LLM HTTP error: {e.code} {e.reason}: {detail}") from e
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"LLM connection error: {e}") from e

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM returned non-JSON response: {raw[:500]}") from e

        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM returned no choices: {payload}")

        message = (choices[0] or {}).get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            tool_calls = []

        return ChatResult(content=str(content), tool_calls=tool_calls)


