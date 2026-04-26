"""Ollama driver — REST API via httpx with streaming support.

Hybrid strategy: probes for tool-call support at runtime. If the model supports
native tool calling (Ollama >=0.3 with a capable model), uses it. Otherwise
falls back to system-prompt action block parsing.
"""

import json
from typing import Generator

import httpx

from ..core.actions import NATIVE_TOOL_SCHEMAS
from .base import BaseDriver, NativeToolCall, ResponseChunk

_DEFAULT_BASE = "http://localhost:11434"
_CHAT_PATH = "/api/chat"
_TAGS_PATH = "/api/tags"


class OllamaDriver(BaseDriver):
    def __init__(self):
        self._base: str = _DEFAULT_BASE
        self._model: str = ""
        self._options: dict = {}
        self._native_tools: bool | None = None  # None = not yet probed

    def configure(
        self,
        api_base: str,
        api_key: str | None,
        model: str,
        options: dict | None = None,
    ) -> None:
        self._base = (api_base or _DEFAULT_BASE).rstrip("/")
        self._model = model
        self._options = options or {}
        self._native_tools = None  # reset probe on reconfigure

    # ------------------------------------------------------------------
    # Capability detection
    # ------------------------------------------------------------------

    def _query_capabilities(self) -> tuple[bool, bool]:
        """Return (supports_tools, supports_thinking) from /api/show."""
        try:
            with httpx.Client(timeout=10) as client:
                r = client.post(
                    f"{self._base}/api/show",
                    json={"name": self._model},
                )
                if r.status_code != 200:
                    return False, False
                caps = r.json().get("capabilities", [])
                return "tools" in caps, "thinking" in caps
        except Exception:
            return False, False

    def supports_native_tools(self) -> bool:
        if self._native_tools is None:
            tools, thinking = self._query_capabilities()
            self._native_tools = tools
            self._has_thinking = thinking
        return self._native_tools

    def _has_thinking_mode(self) -> bool:
        if self._native_tools is None:
            self.supports_native_tools()  # populates _has_thinking
        return getattr(self, "_has_thinking", False)

    def get_native_tool_schema(self) -> list[dict] | None:
        return NATIVE_TOOL_SCHEMAS if self.supports_native_tools() else None

    # ------------------------------------------------------------------
    # Model listing
    # ------------------------------------------------------------------

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(f"{self._base}{_TAGS_PATH}")
                r.raise_for_status()
                data = r.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            raise RuntimeError(f"Failed to list Ollama models: {e}") from e

    # ------------------------------------------------------------------
    # send()
    # ------------------------------------------------------------------

    def send(
        self,
        messages: list[dict],
        system_prompt: str = "",
        stream: bool = True,
    ) -> Generator[ResponseChunk, None, None]:
        body: dict = {
            "model": self._model,
            "messages": messages,
            "stream": stream,
        }
        if system_prompt:
            body["system"] = system_prompt
        if self._options:
            body["options"] = self._options

        if self.supports_native_tools():
            body["tools"] = NATIVE_TOOL_SCHEMAS
            body["tool_choice"] = "required"
            # Disable thinking mode for tool calls: thinking leads to RLHF-driven
            # refusals on file operations for qwen3/glm4 model families.
            if self._has_thinking_mode() and "think" not in self._options:
                body.setdefault("options", {})["think"] = False

        if stream:
            yield from self._stream(body)
        else:
            yield from self._no_stream(body)

    def _stream(self, body: dict) -> Generator[ResponseChunk, None, None]:
        native_tool_calls: list[NativeToolCall] = []
        tokens_in = tokens_out = 0

        with httpx.Client(timeout=None) as client:
            with client.stream("POST", f"{self._base}{_CHAT_PATH}", json=body) as resp:
                resp.raise_for_status()
                for raw_line in resp.iter_lines():
                    if not raw_line.strip():
                        continue
                    try:
                        chunk = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue

                    message = chunk.get("message", {})
                    content = message.get("content", "")
                    tool_calls = message.get("tool_calls", [])

                    if content:
                        yield ResponseChunk(text=content)

                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        native_tool_calls.append(
                            NativeToolCall(
                                name=fn.get("name", ""),
                                params=fn.get("arguments", {}),
                            )
                        )

                    if chunk.get("done"):
                        tokens_in = chunk.get("prompt_eval_count", 0)
                        tokens_out = chunk.get("eval_count", 0)

        yield ResponseChunk(
            done=True,
            native_tool_calls=native_tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=self._model,
        )

    def _no_stream(self, body: dict) -> Generator[ResponseChunk, None, None]:
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self._base}{_CHAT_PATH}", json=body)
            resp.raise_for_status()
            data = resp.json()

        message = data.get("message", {})
        content = message.get("content", "")
        tool_calls_raw = message.get("tool_calls", [])
        native_tool_calls = [
            NativeToolCall(
                name=tc.get("function", {}).get("name", ""),
                params=tc.get("function", {}).get("arguments", {}),
            )
            for tc in tool_calls_raw
        ]
        if content:
            yield ResponseChunk(text=content)
        yield ResponseChunk(
            done=True,
            native_tool_calls=native_tool_calls,
            tokens_in=data.get("prompt_eval_count", 0),
            tokens_out=data.get("eval_count", 0),
            model=self._model,
        )
