from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from assistant.config import CONFIG
from assistant.openai_compat_client import OpenAICompatClient
from memory.sqlite_store import SqliteMemoryStore
from util.logging_utils import log


ToolFunc = Callable[[Dict[str, Any]], str]


class SimpleAgent:
    def __init__(self) -> None:
        self._client = OpenAICompatClient(
            base_url=CONFIG.llm_base_url,
            model=CONFIG.llm_model,
            temperature=CONFIG.llm_temperature,
        )
        self._memory_store = SqliteMemoryStore()
        self._tools: Dict[str, ToolFunc] = {}
        self._tool_schemas: List[Dict[str, Any]] = []

    def set_tools(self, tool_schemas: List[Dict[str, Any]], tool_fns: Dict[str, ToolFunc]) -> None:
        self._tool_schemas = tool_schemas
        self._tools = tool_fns

    def _execute_tool_call(self, tool_call: Dict[str, Any]) -> Tuple[str, str]:
        """
        Returns (tool_call_id, result_text)
        """
        tool_id = str(tool_call.get("id") or "")
        fn = (tool_call.get("function") or {})
        name = fn.get("name") or ""
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
        except json.JSONDecodeError:
            return tool_id, f"Error: invalid JSON arguments for tool '{name}'."

        tool_fn = self._tools.get(name)
        if not tool_fn:
            return tool_id, f"Error: tool '{name}' is not available."

        try:
            return tool_id, str(tool_fn(args))
        except Exception as e:  # noqa: BLE001
            return tool_id, f"Error executing tool '{name}': {e}"

    def run(self, user_text: str) -> str:
        """
        Synchronous, non-streaming agent loop with optional tool-calling.
        """
        user_text = (user_text or "").strip()
        if not user_text:
            return ""

        history = self._memory_store.get_recent_messages(CONFIG.max_context_messages)
        self._memory_store.add_message("user", user_text)

        messages = [*history, {"role": "user", "content": user_text}]

        prefs = self._memory_store.get_preferences()
        system = CONFIG.system_prompt
        if prefs:
            lines = "\n".join(f"- {k}: {v}" for k, v in prefs.items())
            system = f"{system}\n\nUserPreferences:\n{lines}"

        for _ in range(max(0, CONFIG.max_tool_loops)):
            result = self._client.chat_completion(
                messages=messages,
                system=system,
                tools=self._tool_schemas or None,
            )

            if result.content:
                messages.append({"role": "assistant", "content": result.content})

            if not result.tool_calls:
                final_text = result.content.strip()
                if final_text:
                    self._memory_store.add_message("assistant", final_text)
                return final_text

            # Add assistant tool call message (OpenAI-style)
            messages.append({"role": "assistant", "content": None, "tool_calls": result.tool_calls})

            # Execute tools + add tool results
            for tc in result.tool_calls:
                tool_id, tool_out = self._execute_tool_call(tc)
                messages.append({"role": "tool", "tool_call_id": tool_id, "content": tool_out})

        log("Tool loop limit reached; returning last assistant content.")
        # If we hit loop limit, best effort return last assistant content if any
        for m in reversed(messages):
            if m.get("role") == "assistant" and m.get("content"):
                final_text = str(m["content"]).strip()
                if final_text:
                    self._memory_store.add_message("assistant", final_text)
                return final_text
        return ""


