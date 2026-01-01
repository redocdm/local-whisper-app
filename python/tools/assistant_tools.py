from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from memory.sqlite_store import SqliteMemoryStore
from tools.sandbox_fs import SandboxConfig, SandboxFS, SandboxViolation


def build_tools() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Returns (tool_schemas_openai, tool_functions)

    tool_functions are sync callables that accept a single args dict and return str.
    """
    sandbox_root = os.getenv("ASSISTANT_SANDBOX_ROOT") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "sandbox")
    )
    fs = SandboxFS(SandboxConfig(root_dir=sandbox_root))
    mem = SqliteMemoryStore()

    def read_file(args: Dict[str, Any]) -> str:
        try:
            return fs.read_text(str(args.get("path") or ""))
        except SandboxViolation as e:
            return f"Error: {e}"

    def create_file(args: Dict[str, Any]) -> str:
        try:
            rel = fs.write_text_create_only(str(args.get("path") or ""), str(args.get("content") or ""))
            return f"Created: {rel}"
        except SandboxViolation as e:
            return f"Error: {e}"

    def list_dir(args: Dict[str, Any]) -> str:
        try:
            rel = str(args.get("path") or ".")
            items = fs.list_dir(rel)
            return "\n".join(items)
        except SandboxViolation as e:
            return f"Error: {e}"

    def search(args: Dict[str, Any]) -> str:
        try:
            query = str(args.get("query") or "")
            rel_dir = str(args.get("path") or ".")
            hits = fs.search_text(query=query, rel_dir=rel_dir)
            if not hits:
                return "No matches."
            return "\n".join(hits)
        except SandboxViolation as e:
            return f"Error: {e}"

    def set_preference(args: Dict[str, Any]) -> str:
        key = str(args.get("key") or "").strip()
        value = str(args.get("value") or "").strip()
        if not key:
            return "Error: key is required."
        mem.set_preference(key, value)
        return f"Saved preference: {key}"

    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a UTF-8 text file from the assistant sandbox directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative path inside sandbox"}},
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_file",
                "description": "Create a new UTF-8 text file in the assistant sandbox directory (no overwrite).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path inside sandbox"},
                        "content": {"type": "string", "description": "File contents"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "List directory entries in the assistant sandbox directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "Relative directory path inside sandbox"}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Search for an exact substring inside text files in the assistant sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Substring to search for"},
                        "path": {"type": "string", "description": "Relative dir inside sandbox (default '.')"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_preference",
                "description": "Save a durable user preference (key/value) for future conversations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Preference key (e.g. 'tone')"},
                        "value": {"type": "string", "description": "Preference value (e.g. 'concise')"},
                    },
                    "required": ["key", "value"],
                },
            },
        },
    ]

    tool_functions = {
        "read_file": read_file,
        "create_file": create_file,
        "list_dir": list_dir,
        "search": search,
        "set_preference": set_preference,
    }

    # Ensure sandbox directory exists.
    os.makedirs(fs.root_dir, exist_ok=True)

    return tool_schemas, tool_functions


