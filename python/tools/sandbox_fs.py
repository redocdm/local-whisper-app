from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


class SandboxViolation(Exception):
    pass


@dataclass
class SandboxConfig:
    root_dir: str
    max_read_bytes: int = 200_000
    max_write_bytes: int = 200_000
    max_search_results: int = 50


class SandboxFS:
    def __init__(self, config: SandboxConfig) -> None:
        self._cfg = config
        self._root = os.path.abspath(config.root_dir)
        os.makedirs(self._root, exist_ok=True)

    @property
    def root_dir(self) -> str:
        return self._root

    def _resolve(self, rel_path: str) -> str:
        rel_path = (rel_path or "").strip()
        if not rel_path:
            raise SandboxViolation("Path is required.")
        # Disallow absolute paths, UNC, drive letters, and traversal.
        if os.path.isabs(rel_path):
            raise SandboxViolation("Absolute paths are not allowed.")
        if ":" in rel_path:
            raise SandboxViolation("Drive letters are not allowed.")
        norm = os.path.normpath(rel_path).lstrip("\\/")  # normalize and remove leading separators
        if norm.startswith("..") or "\\..\\" in f"\\{norm}\\" or "/../" in f"/{norm}/":
            raise SandboxViolation("Path traversal is not allowed.")
        full = os.path.abspath(os.path.join(self._root, norm))
        if os.path.commonpath([self._root, full]) != self._root:
            raise SandboxViolation("Path escapes sandbox root.")
        return full

    def read_text(self, rel_path: str) -> str:
        full = self._resolve(rel_path)
        if not os.path.exists(full):
            raise SandboxViolation("File does not exist.")
        if os.path.isdir(full):
            raise SandboxViolation("Path is a directory.")
        size = os.path.getsize(full)
        if size > self._cfg.max_read_bytes:
            raise SandboxViolation(f"File too large to read (>{self._cfg.max_read_bytes} bytes).")
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def write_text_create_only(self, rel_path: str, content: str) -> str:
        full = self._resolve(rel_path)
        parent = os.path.dirname(full)
        os.makedirs(parent, exist_ok=True)
        if os.path.exists(full):
            raise SandboxViolation("File already exists (overwrite disabled).")
        data = (content or "").encode("utf-8")
        if len(data) > self._cfg.max_write_bytes:
            raise SandboxViolation(f"Content too large (>{self._cfg.max_write_bytes} bytes).")
        with open(full, "wb") as f:
            f.write(data)
        return os.path.relpath(full, self._root)

    def list_dir(self, rel_path: str = ".") -> List[str]:
        full = self._resolve(rel_path) if rel_path not in (".", "", None) else self._root
        if not os.path.exists(full):
            raise SandboxViolation("Directory does not exist.")
        if not os.path.isdir(full):
            raise SandboxViolation("Path is not a directory.")
        items = []
        for name in sorted(os.listdir(full)):
            if name in (".", ".."):
                continue
            p = os.path.join(full, name)
            suffix = "/" if os.path.isdir(p) else ""
            items.append(f"{name}{suffix}")
        return items

    def search_text(self, query: str, rel_dir: str = ".") -> List[str]:
        query = (query or "").strip()
        if not query:
            raise SandboxViolation("Query is required.")
        base = self._resolve(rel_dir) if rel_dir not in (".", "", None) else self._root
        if not os.path.exists(base) or not os.path.isdir(base):
            raise SandboxViolation("Search directory does not exist.")

        results: List[str] = []
        for root, _dirs, files in os.walk(base):
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, self._root)
                try:
                    if os.path.getsize(full) > self._cfg.max_read_bytes:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, start=1):
                            if query in line:
                                results.append(f"{rel}:{i}:{line.rstrip()}")
                                if len(results) >= self._cfg.max_search_results:
                                    return results
                except Exception:
                    continue
        return results


