# Adding a New LLM Driver to aicli

This guide walks through adding a new vendor driver — from the driver class to
registration, config, and tests. It uses a hypothetical `myprovider` driver as
a running example.

See [API.md](API.md) for the full internal API reference.

---

## Table of Contents

- [Overview](#overview)
- [Step 1 — Create the driver file](#step-1--create-the-driver-file)
- [Step 2 — Implement `configure()`](#step-2--implement-configure)
- [Step 3 — Implement `supports_native_tools()`](#step-3--implement-supports_native_tools)
- [Step 4 — Implement `send()`](#step-4--implement-send)
- [Step 5 — Implement `list_models()`](#step-5--implement-list_models)
- [Step 6 — Register the driver](#step-6--register-the-driver)
- [Step 7 — Add config support](#step-7--add-config-support)
- [Step 8 — Write tests](#step-8--write-tests)
- [Step 9 — Smoke test](#step-9--smoke-test)
- [The `use_tools` parameter](#the-use_tools-parameter)
- [Model-Specific Quirks](#model-specific-quirks)
- [Checklist](#checklist)

---

## Overview

The driver layer is the only place where vendor-specific API details live.
Everything above (plan parsing, permission enforcement, step execution,
rendering) is vendor-agnostic.

In V2, a driver's job is exactly two things:

1. Accept a list of chat messages + a system prompt and stream back
   `ResponseChunk` objects.
2. Respect `use_tools=False` when called by the planner/orchestrator (V2 does
   not use native tool calling — the LLM produces plain-text step plans).

Native tool calling support is retained for any future V1-compatible use cases,
but it is not exercised by the current V2 planner/executor pipeline.

---

## Step 1 — Create the driver file

Create `src/aicli/drivers/myprovider.py`. Start from this skeleton:

```python
"""MyProvider driver."""

from typing import Generator

from .base import BaseDriver, NativeToolCall, ResponseChunk


class MyProviderDriver(BaseDriver):

    def configure(self, api_base, api_key, model, options=None):
        raise NotImplementedError

    def send(self, messages, system_prompt="", stream=True, use_tools=True):
        raise NotImplementedError
        yield  # make it a generator

    def list_models(self):
        raise NotImplementedError

    def supports_native_tools(self):
        return False
```

---

## Step 2 — Implement `configure()`

`configure()` is called once before any `send()`. Store everything needed to
make API requests.

```python
class MyProviderDriver(BaseDriver):

    def __init__(self):
        self._api_base = ""
        self._api_key: str | None = None
        self._model = ""
        self._options: dict = {}

    def configure(
        self,
        api_base: str,
        api_key: str | None,
        model: str,
        options: dict | None = None,
    ) -> None:
        self._api_base = api_base or "https://api.myprovider.example/v1"
        self._api_key = api_key
        self._model = model
        self._options = options or {}
```

The `options` dict passes through from `config.yaml` under
`drivers.myprovider.options`. Use it for model-specific tuning (temperature,
max_tokens, etc.).

---

## Step 3 — Implement `supports_native_tools()`

In V2, this method is not used during normal operation — the planner and
orchestrator always pass `use_tools=False`. Implement it for completeness
and potential future use.

### Option A — Always false

```python
def supports_native_tools(self) -> bool:
    return False
```

### Option B — Declare support (not used in V2 planner mode)

If the API supports OpenAI-style function calling, return `True` and
implement `get_native_tool_schema()`:

```python
from ..core.actions import NATIVE_TOOL_SCHEMAS

def supports_native_tools(self) -> bool:
    return True

def get_native_tool_schema(self) -> list[dict] | None:
    return NATIVE_TOOL_SCHEMAS
```

### Option C — Runtime detection

Query the model's capabilities and cache the result:

```python
def supports_native_tools(self) -> bool:
    if not hasattr(self, "_tools_supported"):
        self._tools_supported = self._check_capability()
    return self._tools_supported
```

---

## Step 4 — Implement `send()`

`send()` is a **generator function** that must:

- Yield `ResponseChunk(text=..., done=False)` for each streamed text segment.
- Yield exactly one `ResponseChunk(done=True, ...)` as the final chunk.
- Respect `use_tools=False` by omitting tool schemas from the request.

### Minimal streaming example

```python
import json
import httpx
from ..core.actions import NATIVE_TOOL_SCHEMAS
from .base import BaseDriver, NativeToolCall, ResponseChunk


class MyProviderDriver(BaseDriver):

    def send(
        self,
        messages: list[dict],
        system_prompt: str = "",
        stream: bool = True,
        use_tools: bool = True,
    ) -> Generator[ResponseChunk, None, None]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "messages": self._build_messages(messages, system_prompt),
            "stream": stream,
        }

        # Only add tools when use_tools=True (V2 planner always passes False)
        if use_tools and self.supports_native_tools():
            body["tools"] = NATIVE_TOOL_SCHEMAS
            body["tool_choice"] = "required"

        if stream:
            yield from self._stream(headers, body)
        else:
            yield from self._no_stream(headers, body)

    def _build_messages(self, messages, system_prompt):
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        result.extend(messages)
        return result
```

### Streaming loop (OpenAI-compatible SSE)

```python
    def _stream(self, headers, body):
        tool_calls: list[NativeToolCall] = []
        tokens_in = tokens_out = 0

        with httpx.Client(timeout=None) as client:
            with client.stream(
                "POST",
                f"{self._api_base}/chat/completions",
                headers=headers,
                json=body,
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    line = line.strip()
                    if not line or line == "data: [DONE]":
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content") or ""
                    if text:
                        yield ResponseChunk(text=text)

                    for tc in delta.get("tool_calls", []):
                        fn = tc.get("function", {})
                        tool_calls.append(NativeToolCall(
                            name=fn.get("name", ""),
                            params=self._parse_args(fn.get("arguments", {})),
                            call_id=tc.get("id", ""),
                        ))

                    if "usage" in chunk:
                        tokens_in = chunk["usage"].get("prompt_tokens", 0)
                        tokens_out = chunk["usage"].get("completion_tokens", 0)

        yield ResponseChunk(
            done=True,
            native_tool_calls=tool_calls,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=self._model,
        )

    def _parse_args(self, raw) -> dict:
        """Handle arguments as dict or JSON-encoded string."""
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                import json as _json
                return _json.loads(raw)
            except Exception:
                return {}
        return {}
```

### Non-streaming fallback

```python
    def _no_stream(self, headers, body):
        body = dict(body)
        body["stream"] = False
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{self._api_base}/chat/completions",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content") or ""
        tool_calls_raw = message.get("tool_calls", [])
        tool_calls = [
            NativeToolCall(
                name=tc.get("function", {}).get("name", ""),
                params=self._parse_args(tc.get("function", {}).get("arguments", {})),
                call_id=tc.get("id", ""),
            )
            for tc in tool_calls_raw
        ]
        if content:
            yield ResponseChunk(text=content)
        usage = data.get("usage", {})
        yield ResponseChunk(
            done=True,
            native_tool_calls=tool_calls,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            model=self._model,
        )
```

---

## Step 5 — Implement `list_models()`

```python
def list_models(self) -> list[str]:
    headers = {"Authorization": f"Bearer {self._api_key}"}
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{self._api_base}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return sorted(m["id"] for m in data.get("data", []))
```

If the API has no model-listing endpoint, return a hardcoded list:

```python
def list_models(self) -> list[str]:
    return ["myprovider-fast", "myprovider-large"]
```

---

## Step 6 — Register the driver

Open `src/aicli/drivers/registry.py` and add your driver:

```python
from .myprovider import MyProviderDriver   # ← add this import

_REGISTRY: dict[str, type[BaseDriver]] = {
    "ollama":      OllamaDriver,
    "gemini":      GeminiDriver,
    "claude":      ClaudeDriver,
    "openai":      OpenAIDriver,
    "myprovider":  MyProviderDriver,       # ← add this entry
}
```

The registry key is the driver prefix in `--model`:

```bash
aicli --model myprovider/model-name-v1
```

---

## Step 7 — Add config support

Add a default entry in `src/aicli/config.py` under `_DEFAULTS["drivers"]`:

```python
_DEFAULTS = {
    ...
    "drivers": {
        "ollama":     {"api_base": "http://localhost:11434"},
        "gemini":     {"api_key_env": "GEMINI_API_KEY"},
        "claude":     {"api_key_env": "ANTHROPIC_API_KEY"},
        "openai":     {"api_key_env": "OPENAI_API_KEY"},
        "myprovider": {"api_key_env": "MYPROVIDER_API_KEY"},   # ← add
    },
}
```

Users set the key:

```bash
export MYPROVIDER_API_KEY="sk-..."
```

Or in `~/.config/aicli/config.yaml`:

```yaml
drivers:
  myprovider:
    api_key_env: MYPROVIDER_API_KEY
    api_base: https://api.myprovider.example/v1
    options:
      temperature: 0.2
```

---

## Step 8 — Write tests

Create `tests/test_drivers/test_myprovider.py`:

```python
"""Integration tests — require API credentials (skipped otherwise)."""

import os
import pytest
from aicli.drivers.myprovider import MyProviderDriver

API_KEY = os.environ.get("MYPROVIDER_API_KEY", "")
TEST_MODEL = "myprovider-fast"


@pytest.fixture(scope="module")
def driver():
    if not API_KEY:
        pytest.skip("MYPROVIDER_API_KEY not set")
    d = MyProviderDriver()
    d.configure(api_base="", api_key=API_KEY, model=TEST_MODEL)
    return d


def test_list_models(driver):
    models = driver.list_models()
    assert isinstance(models, list) and len(models) > 0


def test_supports_native_tools(driver):
    assert isinstance(driver.supports_native_tools(), bool)


def test_simple_stream(driver):
    messages = [{"role": "user", "content": "Reply with just: PONG"}]
    chunks = []
    done_chunk = None
    for chunk in driver.send(messages, stream=True, use_tools=False):
        if chunk.done:
            done_chunk = chunk
        else:
            chunks.append(chunk.text)
    assert "".join(chunks).strip()
    assert done_chunk is not None and done_chunk.done


def test_use_tools_false_sends_no_schema(driver):
    """Verify use_tools=False produces a plain text response (no tool calls)."""
    messages = [{"role": "user", "content": "What is 2+2?"}]
    done_chunk = None
    for chunk in driver.send(messages, stream=True, use_tools=False):
        if chunk.done:
            done_chunk = chunk
    assert done_chunk is not None
    # With use_tools=False, no tool calls should be returned
    assert done_chunk.native_tool_calls == []


def test_v2_planner_call(driver):
    """Simulate what the V2 Planner does: plain text response expected."""
    from aicli.core.planner import load_system_prompt
    sp = load_system_prompt()
    messages = [{"role": "user", "content":
        "Produce a step plan using ONLY step blocks. No prose.\n\n"
        "TASK: Read /tmp/test.txt and write a summary to /tmp/summary.md"}]
    text = ""
    for chunk in driver.send(messages, system_prompt=sp, stream=True, use_tools=False):
        if not chunk.done:
            text += chunk.text
    # Should contain at least one recognized keyword
    from aicli.core.plan_parser import KEYWORDS
    assert any(kw in text.upper() for kw in KEYWORDS), \
        f"Expected a plan with step keywords; got: {text[:200]}"
```

---

## Step 9 — Smoke test

```bash
# Verify the driver is registered and can list models
aicli --model myprovider --list-models

# Basic question (no file ops)
echo "What is the capital of France?" | \
  aicli --model myprovider/myprovider-fast --no-markdown

# Dry-run: see the plan the model produces
echo "Read /tmp/test.txt and summarize it to /tmp/summary.md" | \
  aicli --model myprovider/myprovider-fast \
        --include-directories /tmp \
        --dry-run

# Full execution
echo "Read /tmp/test.txt and summarize it to /tmp/summary.md" | \
  aicli --model myprovider/myprovider-fast \
        --include-directories /tmp \
        --auto-approve
```

---

## The `use_tools` parameter

In V2, all LLM calls go through `driver.send(..., use_tools=False)`. This tells
the driver not to include native tool schemas in the request body. The model
receives only the system prompt and messages, and responds with plain text (the
step plan, or the PROMPT/GENCODE response).

**Drivers MUST respect this flag.** If `use_tools=False`, omit `"tools"` and
`"tool_choice"` from the request entirely, even if `supports_native_tools()`
returns `True`.

```python
def send(self, messages, system_prompt="", stream=True, use_tools=True):
    body = {
        "model": self._model,
        "messages": ...,
        "stream": stream,
    }
    # ← IMPORTANT: only add tools when explicitly requested
    if use_tools and self.supports_native_tools():
        body["tools"] = NATIVE_TOOL_SCHEMAS
        body["tool_choice"] = "required"
    ...
```

---

## Model-Specific Quirks

Document model quirks when you discover them. Known patterns:

### Thinking models

Models with extended reasoning modes (chain-of-thought, thinking tokens) may
re-activate RLHF refusals for file operations when thinking mode is on. The
Ollama driver disables thinking (`options.think = false`) automatically for
such models. If your provider has a similar mode, consider disabling it when
`use_tools=False` (planner mode) to get cleaner plain-text responses.

### Arguments as JSON strings

OpenAI's streaming API returns `function.arguments` as a JSON-encoded string.
Non-streaming responses may return a dict. Handle both with `_parse_args()`.

### Empty function names

Some models return empty string for the function name. aicli's core handles
this through argument-key heuristics (`_infer_action_type` in `cli.py`).

### Models ignoring system prompts

Some models are fine-tuned to override system prompt constraints with
"helpful" responses. If the plan parser returns no steps (the warning
"No plan steps found" appears), the model is not following the planner format.

Workarounds:
- Try a different model for the planner role (`--model`).
- Embed the format instruction in the user message (the Planner class already
  does this via the "Produce a step plan using ONLY step blocks..." prefix).
- Use a more directive custom system prompt (`--system-prompt-file`).

---

## Checklist

- [ ] `src/aicli/drivers/myprovider.py` created, all four abstract methods implemented
- [ ] `send()` signature includes `use_tools: bool = True` parameter
- [ ] `send()` only adds tool schemas when `use_tools=True` (not when `False`)
- [ ] `send()` always yields a terminal `ResponseChunk(done=True)` as the last item
- [ ] `send()` handles both `stream=True` and `stream=False`
- [ ] Driver registered in `src/aicli/drivers/registry.py`
- [ ] Default config entry added in `src/aicli/config.py`
- [ ] `tests/test_drivers/test_myprovider.py` written (skips gracefully without credentials)
- [ ] `test_use_tools_false_sends_no_schema` passes
- [ ] Existing unit tests still pass: `python -m pytest tests/test_parser.py tests/test_executor.py tests/test_permissions.py`
- [ ] Dry-run smoke test passes (plan is produced with step keywords)
- [ ] Full execution smoke test passes (file written to /tmp)
