"""Minimal streaming client for the local Ollama REST API."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Iterator

import requests

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


class ToolsUnsupported(OllamaError):
    pass


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.trust_env = False  # never route localhost through a system proxy

    # ------------------------------------------------------------- status
    def ping(self, timeout: float = 2.0) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/api/version", timeout=timeout)
            return r.ok
        except requests.RequestException:
            return False

    def version(self) -> str:
        try:
            return self._session.get(f"{self.base_url}/api/version", timeout=2).json().get("version", "?")
        except Exception:
            return "?"

    def list_models(self) -> list[str]:
        try:
            r = self._session.get(f"{self.base_url}/api/tags", timeout=4)
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        except Exception as exc:
            log.debug("list_models failed: %s", exc)
            return []

    # --------------------------------------------------------------- chat
    def chat_stream(self, model: str, messages: list[dict], *, tools: list | None = None,
                    options: dict | None = None, keep_alive: str | None = None,
                    stop: threading.Event | None = None) -> Iterator[tuple[str, object]]:
        """Yields ("token", str), ("thinking", str), ("tool_calls", list) and finally ("done", dict)."""
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        if keep_alive:
            payload["keep_alive"] = keep_alive
        try:
            resp = self._session.post(f"{self.base_url}/api/chat", json=payload, stream=True,
                                      timeout=(5, 300))
        except requests.ConnectionError as exc:
            raise OllamaError("Cannot reach Ollama. Is it running? (ollama serve)") from exc
        if resp.status_code != 200:
            try:
                err = resp.json().get("error", resp.text)
            except Exception:
                err = resp.text
            if tools and "tools" in str(err).lower():
                raise ToolsUnsupported(err)
            if "not found" in str(err).lower():
                raise OllamaError(f"Model '{model}' is not installed. Run: ollama pull {model}")
            raise OllamaError(f"Ollama error {resp.status_code}: {err}")

        tool_calls: list = []
        final: dict = {}
        try:
            for line in resp.iter_lines():
                if stop is not None and stop.is_set():
                    break
                if not line:
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise OllamaError(chunk["error"])
                msg = chunk.get("message") or {}
                if msg.get("thinking"):
                    yield "thinking", msg["thinking"]
                if msg.get("content"):
                    yield "token", msg["content"]
                if msg.get("tool_calls"):
                    tool_calls.extend(msg["tool_calls"])
                if chunk.get("done"):
                    final = chunk
                    break
        finally:
            resp.close()
        if tool_calls:
            yield "tool_calls", tool_calls
        yield "done", final

    def chat_json(self, model: str, messages: list[dict], options: dict | None = None,
                  keep_alive: str | None = None, timeout: float = 120) -> dict:
        payload = {"model": model, "messages": messages, "stream": False, "format": "json",
                   "options": options or {"temperature": 0.1}}
        if keep_alive:
            payload["keep_alive"] = keep_alive
        r = self._session.post(f"{self.base_url}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "{}")
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    def pull(self, model: str) -> Iterator[dict]:
        with self._session.post(f"{self.base_url}/api/pull", json={"model": model, "stream": True},
                                stream=True, timeout=(5, 3600)) as r:
            for line in r.iter_lines():
                if line:
                    yield json.loads(line)


def start_ollama_server() -> bool:
    """Try to launch `ollama serve` in the background (hidden). Returns True if a process was started."""
    candidates = [shutil.which("ollama")]
    if sys.platform == "win32":
        candidates.append(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"))
    for exe in candidates:
        if exe and os.path.exists(exe):
            try:
                flags = 0x08000000 | 0x00000008 if sys.platform == "win32" else 0  # no window, detached
                subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, creationflags=flags)
                log.info("Started Ollama server: %s", exe)
                return True
            except Exception as exc:
                log.warning("Could not start Ollama: %s", exc)
    return False
