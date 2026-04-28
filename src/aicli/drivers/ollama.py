"""Ollama driver — REST API via httpx with streaming support.

Hybrid strategy: probes for tool-call support at runtime. If the model supports
native tool calling (Ollama >=0.3 with a capable model), uses it. Otherwise
falls back to system-prompt action block parsing.
"""

import json
import time
from typing import Generator

import httpx

from ..core.actions import NATIVE_TOOL_SCHEMAS
from ..output.tracer import trace
from .base import BaseDriver, NativeToolCall, ResponseChunk

_DEFAULT_BASE = "http://localhost:11434"
_CHAT_PATH = "/api/chat"
_TAGS_PATH = "/api/tags"

# Seconds to wait for the next streamed chunk before giving up.
# Thinking models can be silent for a long time; callers may override.
DEFAULT_STREAM_READ_TIMEOUT = 600


class OllamaDriver(BaseDriver):
    def __init__(self):
        self._base: str = _DEFAULT_BASE
        self._model: str = ""
        self._options: dict = {}
        self._native_tools: bool | None = None  # None = not yet probed
        self._stream_read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT

    def configure(
        self,
        api_base: str,
        api_key: str | None,
        model: str,
        options: dict | None = None,
        stream_read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT,
    ) -> None:
        self._base = (api_base or _DEFAULT_BASE).rstrip("/")
        self._model = model
        self._options = options or {}
        self._native_tools = None  # reset probe on reconfigure
        self._stream_read_timeout = stream_read_timeout

    # ------------------------------------------------------------------
    # Capability detection
    # ------------------------------------------------------------------

    def _query_capabilities(self) -> tuple[bool, bool]:
        """Return (supports_tools, supports_thinking) from /api/show."""
        trace("CAPABILITY_PROBE_START", f"model={self._model}")
        try:
            with httpx.Client(timeout=10) as client:
                r = client.post(
                    f"{self._base}/api/show",
                    json={"name": self._model},
                )
                if r.status_code != 200:
                    trace("CAPABILITY_PROBE_DONE", f"model={self._model} status={r.status_code} tools=False thinking=False")
                    return False, False
                caps = r.json().get("capabilities", [])
                tools, thinking = "tools" in caps, "thinking" in caps
                trace("CAPABILITY_PROBE_DONE", f"model={self._model} tools={tools} thinking={thinking}")
                return tools, thinking
        except Exception as e:
            trace("CAPABILITY_PROBE_ERROR", f"model={self._model} error={e}")
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
        use_tools: bool = True,
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

        if use_tools and self.supports_native_tools():
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
        first_chunk = True
        # Track time of last received TEXT token.  Thinking models keep the network
        # connection alive with empty keepalive chunks, so httpx's read timeout never
        # fires.  We enforce our own silence timeout at the application level.
        last_text_time = time.monotonic()
        timed_out = False

        # connect=10s, read=30s (just for network drops; text silence handled below)
        timeout = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
        trace("STREAM_CONNECT", f"model={self._model} silence_timeout={self._stream_read_timeout}s")

        try:
            with httpx.Client(timeout=timeout) as client:
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
                            last_text_time = time.monotonic()
                            if first_chunk:
                                trace("STREAM_FIRST_CHUNK", f"model={self._model}")
                                first_chunk = False
                            yield ResponseChunk(text=content)
                        elif self._stream_read_timeout > 0:
                            # No text in this chunk (keepalive/thinking).  Check silence.
                            elapsed = time.monotonic() - last_text_time
                            if elapsed > self._stream_read_timeout:
                                trace("STREAM_SILENCE_TIMEOUT",
                                      f"model={self._model} elapsed={elapsed:.0f}s")
                                timed_out = True
                                break

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
                            trace("STREAM_DONE",
                                  f"model={self._model} tokens_in={tokens_in} tokens_out={tokens_out}")

        except (httpx.ReadTimeout, httpx.TimeoutException) as e:
            trace("STREAM_NETWORK_TIMEOUT", f"model={self._model} error={e}")
            timed_out = True

        if timed_out:
            trace("STREAM_ABORTED", f"model={self._model}")

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
