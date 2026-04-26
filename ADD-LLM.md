# Adding a New LLM Driver to aicli

This guide walks through the full process of adding a new vendor driver —
from writing the driver class to registering it, wiring up config, and
running tests. It uses a hypothetical `myprovider` driver as a running example.

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
- [Native Tool Calling](#native-tool-calling)
- [System Prompt Fallback](#system-prompt-fallback)
- [Model-Specific Quirks](#model-specific-quirks)
- [Checklist](#checklist)

---

## Overview

The driver layer is the only place where vendor-specific API details live.
Everything above (action parsing, permission enforcement, session history,
rendering) is vendor-agnostic.

A driver's job is exactly two things:

1. Accept a list of chat messages + a system prompt and stream back
   `ResponseChunk` objects.
2. Optionally expose native tool/function-calling if the API supports it.

The rest is handled by aicli's core.

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

    def send(self, messages, system_prompt="", stream=True):
        raise NotImplementedError
        yield  # make it a generator

    def list_models(self):
        raise NotImplementedError

    def supports_native_tools(self):
        return False
```

---

## Step 2 — Implement `configure()`

`configure()` is called once before any `send()` call. Store everything you
need to make API requests.

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

The `options` dict is passed through from `config.yaml` under
`drivers.myprovider.options`. Use it for model-specific tuning (e.g.
`temperature`, `think`, `max_tokens`).

---

## Step 3 — Implement `supports_native_tools()`

Decide whether your driver will use native tool/function calling.

### Option A — Native tools supported

If the API supports OpenAI-style function calling, return `True` and also
implement `get_native_tool_schema()`:

```python
from ..core.actions import NATIVE_TOOL_SCHEMAS

def supports_native_tools(self) -> bool:
    return True

def get_native_tool_schema(self) -> list[dict] | None:
    return NATIVE_TOOL_SCHEMAS
```

`NATIVE_TOOL_SCHEMAS` is a list of five JSON Schema function definitions
(read_file, write_file, list_directory, execute, search_files). Most
OpenAI-compatible APIs accept this format directly. See
`src/aicli/core/actions.py` for the full schema.

### Option B — No native tools

Return `False`. aicli will inject `ACTION_SYSTEM_PROMPT` into the system
prompt, which teaches the model the `<aicli_action>` XML block format.

```python
def supports_native_tools(self) -> bool:
    return False
```

### Option C — Detect at runtime (recommended for Ollama-style APIs)

Query the model's capabilities and cache the result:

```python
def supports_native_tools(self) -> bool:
    if not hasattr(self, "_tools_supported"):
        self._tools_supported = self._check_capability()
    return self._tools_supported

def _check_capability(self) -> bool:
    # Query your API for model info and check for tool support
    ...
```

---

## Step 4 — Implement `send()`

`send()` is a **generator function** that must:

- Yield `ResponseChunk(text=..., done=False)` for each streamed text segment.
- Yield exactly one `ResponseChunk(done=True, ...)` as the final chunk.
- Populate `native_tool_calls` on the done chunk when native tools are used.

### Minimal streaming example (REST API)

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
        if self.supports_native_tools():
            body["tools"] = NATIVE_TOOL_SCHEMAS
            body["tool_choice"] = "required"

        if stream:
            yield from self._stream(headers, body)
        else:
            yield from self._no_stream(headers, body)

    def _build_messages(self, messages, system_prompt):
        """Convert aicli messages to provider format."""
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        result.extend(messages)
        return result
```

### Streaming loop

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

                    # Extract text delta
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content") or ""
                    if text:
                        yield ResponseChunk(text=text)

                    # Extract tool calls (OpenAI format)
                    for tc in delta.get("tool_calls", []):
                        fn = tc.get("function", {})
                        tool_calls.append(NativeToolCall(
                            name=fn.get("name", ""),
                            params=fn.get("arguments", {}),
                            call_id=tc.get("id", ""),
                        ))

                    # Usage (may appear on any chunk or the last one)
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
                params=tc.get("function", {}).get("arguments", {}),
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

### Tool call argument parsing

Some APIs return `arguments` as a JSON-encoded string rather than a dict.
Handle both:

```python
import json as json_module

def _parse_arguments(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json_module.loads(raw)
        except json_module.JSONDecodeError:
            return {}
    return {}
```

Use `_parse_arguments(fn.get("arguments", {}))` when building `NativeToolCall`.

---

## Step 5 — Implement `list_models()`

Return a list of model name strings. Users see this output from `--list-models`.

```python
def list_models(self) -> list[str]:
    headers = {"Authorization": f"Bearer {self._api_key}"}
    with httpx.Client(timeout=10) as client:
        resp = client.get(f"{self._api_base}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return sorted(m["id"] for m in data.get("data", []))
```

If the API does not have a model-listing endpoint, return a hardcoded list:

```python
def list_models(self) -> list[str]:
    return ["myprovider-model-v1", "myprovider-model-v2"]
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

The key (`"myprovider"`) is what users write before the `/` in `--model`:

```bash
aicli --model myprovider/model-name-v1
```

---

## Step 7 — Add config support

Add a default config entry in `src/aicli/config.py` under `_DEFAULTS`:

```python
_DEFAULTS = {
    ...
    "drivers": {
        "ollama":     {"api_base": "http://localhost:11434"},
        "gemini":     {"api_key_env": "GEMINI_API_KEY"},
        "claude":     {"api_key_env": "ANTHROPIC_API_KEY"},
        "openai":     {"api_key_env": "OPENAI_API_KEY"},
        "myprovider": {"api_key_env": "MYPROVIDER_API_KEY"},   # ← add this
    },
}
```

Users can then set their API key with:

```bash
export MYPROVIDER_API_KEY="sk-..."
```

Or add it to `~/.config/aicli/config.yaml`:

```yaml
drivers:
  myprovider:
    api_key_env: MYPROVIDER_API_KEY   # read from environment
    # api_key: sk-literal-value       # or hardcode (not recommended)
    api_base: https://api.myprovider.example/v1
    options:
      temperature: 0.2
```

---

## Step 8 — Write tests

Create `tests/test_drivers/test_myprovider.py`. At minimum test:

1. **`list_models()`** — returns a non-empty list of strings.
2. **`supports_native_tools()`** — returns a bool.
3. **`send()` streaming** — yields at least one text chunk and exactly one done chunk.
4. **System prompt injection** — the system prompt appears in the outgoing request.

### Example test file

```python
"""Integration tests for the MyProvider driver — requires API credentials."""

import os
import pytest
from aicli.drivers.myprovider import MyProviderDriver

API_KEY = os.environ.get("MYPROVIDER_API_KEY", "")
TEST_MODEL = "myprovider-model-v1"


@pytest.fixture(scope="module")
def driver():
    if not API_KEY:
        pytest.skip("MYPROVIDER_API_KEY not set")
    d = MyProviderDriver()
    d.configure(api_base="", api_key=API_KEY, model=TEST_MODEL)
    return d


def test_list_models(driver):
    models = driver.list_models()
    assert isinstance(models, list)
    assert len(models) > 0
    assert all(isinstance(m, str) for m in models)


def test_supports_native_tools_returns_bool(driver):
    result = driver.supports_native_tools()
    assert isinstance(result, bool)


def test_simple_stream(driver):
    messages = [{"role": "user", "content": "Reply with just: PONG"}]
    chunks = []
    done_chunk = None
    for chunk in driver.send(messages, stream=True):
        if chunk.done:
            done_chunk = chunk
        else:
            chunks.append(chunk.text)

    full_text = "".join(chunks)
    assert len(full_text) > 0
    assert done_chunk is not None
    assert done_chunk.done is True


def test_system_prompt_applied(driver):
    messages = [{"role": "user", "content": "What is 2+2?"}]
    system = "Always respond with only the number, no words."
    chunks = []
    for chunk in driver.send(messages, system_prompt=system, stream=True):
        if not chunk.done:
            chunks.append(chunk.text)
    full_text = "".join(chunks)
    assert len(full_text) > 0


def test_native_tool_write_file(driver):
    """Only run if the driver declares native tool support."""
    if not driver.supports_native_tools():
        pytest.skip("Native tools not supported by this driver")

    messages = [{"role": "user", "content": "Write Hello to /tmp/test.txt"}]
    chunks = []
    done_chunk = None
    for chunk in driver.send(messages, stream=True):
        if chunk.done:
            done_chunk = chunk
        else:
            chunks.append(chunk.text)

    assert done_chunk is not None
    assert isinstance(done_chunk.native_tool_calls, list)
    # The model should have called write_file (or a recognisable variant)
    if done_chunk.native_tool_calls:
        tc = done_chunk.native_tool_calls[0]
        assert tc.name or tc.params  # at minimum something was returned
```

### Running the tests

```bash
# All tests (unit + integration)
python3.11 -m pytest tests/ -v

# Just the new driver (skips automatically if no API key)
python3.11 -m pytest tests/test_drivers/test_myprovider.py -v

# Unit tests only — no external calls
python3.11 -m pytest tests/test_parser.py tests/test_executor.py tests/test_permissions.py -v
```

---

## Step 9 — Smoke test

Once the driver is registered and configured, test it end-to-end:

```bash
# Confirm the driver is recognised and can list models
aicli --model myprovider --list-models

# Basic question (no file ops)
echo "What is the capital of France?" | \
  aicli --model myprovider/myprovider-model-v1 --no-markdown

# Agentic write (requires a model with tool support)
echo "Write Hello World to /tmp/myprovider_test.txt" | \
  aicli --model myprovider/myprovider-model-v1 \
        --include-directories /tmp \
        --auto-approve
cat /tmp/myprovider_test.txt
```

---

## Native Tool Calling

### How aicli uses tools

When `driver.supports_native_tools()` returns `True`:

1. `NATIVE_TOOL_SCHEMAS` is added to the request body as `tools`.
2. `tool_choice: "required"` is added to force the model to call a tool.
3. The system prompt is the short `NATIVE_TOOLS_HINT` (not the XML action format).
4. After streaming, the done chunk's `native_tool_calls` list is processed
   by `_native_call_to_action_request()` in `cli.py`.

The tool schemas use standard JSON Schema / OpenAI function format and are
accepted by most APIs without modification.

### Parameter name normalization

Models often return parameter names that differ from the schema
(`file_path` instead of `path`, `cmd` instead of `command`, etc.).
aicli normalizes these automatically via `_PARAM_ALIASES` in `cli.py`.
If your model uses unusual parameter names consistently, add them there.

### Function name inference

If a model returns an empty function name or a non-standard one
(e.g. `"fs.writeFile"`, `"create_file"`, `"function5"`), aicli's
`_infer_action_type()` uses fuzzy name matching and argument-key
heuristics to determine the intended action. You do not need to handle
this in the driver — return whatever the API gives you.

---

## System Prompt Fallback

When `supports_native_tools()` returns `False`, aicli automatically injects
`ACTION_SYSTEM_PROMPT` which teaches the model the `<aicli_action>` XML format.

This approach works with any model that can follow formatting instructions, but
reliability varies. Models with heavy RLHF training against file operations (e.g.
most qwen3.x variants) may ignore the format and respond with plain text instead.

To improve reliability for a system-prompt-only model:

- Use a stronger, more directive system prompt (see `system_prompt.py`).
- Include few-shot examples in the `--system-prompt-file`.
- Consider whether the model's native tool calling (if any) is preferable.

---

## Model-Specific Quirks

Document any quirks about your provider's models in this section when you
discover them during testing. Here are the patterns found in the existing drivers:

### Thinking models (qwen3, glm4)

Models with a `thinking` capability (Ollama's internal extended reasoning) will
reason their way into RLHF-driven refusals for file operations when thinking mode
is on. The Ollama driver disables thinking (`options.think = false`) automatically
for these models when native tools are active.

If your provider has a similar reasoning/thinking mode, check whether disabling
it improves tool call reliability.

### Empty function names

The `batiai/qwen3.6-35b` model consistently returns empty string as the function
name in tool calls. aicli handles this through argument-key heuristics. If your
model does the same, no driver change is needed — the core layer handles it.

### Arguments as JSON strings

OpenAI's streaming API returns `function.arguments` as a JSON-encoded string
rather than a parsed dict. Parse it with `json.loads()` before building
`NativeToolCall`. Non-streaming responses may return a dict directly.

### Thinking tokens in streaming output

Some models interleave `<think>...</think>` tokens in their text output. These
appear in streamed `ResponseChunk.text` and will be rendered to the user's terminal.
If you want to suppress them, strip them in `_stream()` before yielding chunks.

---

## Checklist

Use this checklist when submitting a new driver:

- [ ] `src/aicli/drivers/myprovider.py` created and implements all four abstract methods
- [ ] Driver registered in `src/aicli/drivers/registry.py`
- [ ] Default config entry added in `src/aicli/config.py` (`_DEFAULTS["drivers"]`)
- [ ] `configure()` sets `api_base`, `api_key`, `model`, and `options`
- [ ] `send()` always yields a terminal `ResponseChunk(done=True)` as the last item
- [ ] `send()` handles both `stream=True` and `stream=False`
- [ ] `supports_native_tools()` returns the correct value for the model family
- [ ] If native tools: `NATIVE_TOOL_SCHEMAS` is passed in the request body
- [ ] If native tools: `tool_choice: "required"` is set in the request body
- [ ] `list_models()` returns a non-empty list of strings (or a clear stub)
- [ ] `tests/test_drivers/test_myprovider.py` written and skips gracefully if no API key
- [ ] All existing unit tests still pass: `python3.11 -m pytest tests/test_parser.py tests/test_executor.py tests/test_permissions.py`
- [ ] End-to-end smoke test passes (basic question + file write if native tools)
