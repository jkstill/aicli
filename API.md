# aicli Internal API Reference

This document describes the internal layers and contracts that connect the CLI
frontend, the core execution engine, and the vendor drivers. It is intended for
contributors, driver authors, and anyone extending aicli's behaviour.

For adding a new LLM provider see [ADD-LLM.md](ADD-LLM.md).

---

## Table of Contents

- [Architectural Overview](#architectural-overview)
- [Layer 1 — CLI Frontend (`cli.py`)](#layer-1--cli-frontend)
- [Layer 2 — Core Layer](#layer-2--core-layer)
  - [Action Schema (`core/actions.py`)](#action-schema)
  - [Action Parser (`core/parser.py`)](#action-parser)
  - [Executor (`core/executor.py`)](#executor)
  - [Session (`core/session.py`)](#session)
  - [System Prompts (`core/system_prompt.py`)](#system-prompts)
- [Layer 3 — Driver Interface (`drivers/base.py`)](#layer-3--driver-interface)
- [Ollama Driver (`drivers/ollama.py`)](#ollama-driver)
- [Driver Registry (`drivers/registry.py`)](#driver-registry)
- [Config (`config.py`)](#config)
- [Output Layer (`output/`)](#output-layer)
- [Data Flow — One Full Turn](#data-flow--one-full-turn)
- [Error Handling](#error-handling)

---

## Architectural Overview

```
┌──────────────────────────────────────────────────┐
│  cli.py  (main, run_turn, _stream_response, ...) │
│  Owns: session, renderer, logger, executor       │
└──────────────────┬───────────────────────────────┘
                   │
         ┌─────────┴──────────┐
         │  Core Layer        │
         │  actions.py        │  ActionRequest / ActionResult / ActionType
         │  parser.py         │  XML action block → ActionRequest
         │  executor.py       │  ActionRequest → ActionResult (+ permission check)
         │  session.py        │  conversation history
         │  system_prompt.py  │  system prompt builders
         └─────────┬──────────┘
                   │
         ┌─────────┴──────────┐
         │  Driver            │
         │  base.py           │  BaseDriver ABC
         │  ollama.py         │  Ollama REST implementation
         │  registry.py       │  name → driver class
         └────────────────────┘
```

**Key invariant:** The executor and the driver never talk to each other.
The driver sends prompts and receives text/tool-calls. The executor parses
text and performs actions. This separation is enforced by the call graph.

---

## Layer 1 — CLI Frontend

**File:** `src/aicli/cli.py`

### `main()` — Click command

Entry point. Responsibilities:

1. Load config and parse CLI flags.
2. Resolve `driver/model-name` via `_parse_model()`.
3. Instantiate and configure the driver.
4. Build the `Executor` with permission settings.
5. Build the effective system prompt.
6. Dispatch to pipe / file / REPL mode.

### `run_turn(prompt, session, driver, executor, renderer, logger, ...)`

Executes one full user prompt, including any number of action rounds.

```
for each round (up to max_action_rounds=10):
    1. send session messages to driver (streaming)
    2. parse actions from response (native tools or XML blocks)
    3. record assistant turn in session
    4. if no actions: done
    5. for each action:
         a. print summary
         b. confirm (unless auto_approve)
         c. executor.execute(req)  →  ActionResult
         d. print result
    6. add combined results to session as a user message
    7. loop (model sees the results and continues)
```

### `_stream_response(driver, messages, system_prompt, renderer)`

Drives the streaming generator from the driver. Writes text chunks to the
renderer buffer. Returns `(full_text, done_chunk)` when the `done=True`
chunk arrives.

```python
full_text, done_chunk = _stream_response(driver, messages, system_prompt, renderer)
# full_text: accumulated text (same as renderer._buffer before finalize())
# done_chunk: ResponseChunk(done=True, native_tool_calls=[...], tokens_in=N, ...)
```

### `_native_call_to_action_request(tc: NativeToolCall) → ActionRequest | None`

Converts a `NativeToolCall` from the driver into an `ActionRequest` understood
by the executor. Two-step process:

1. **Normalize parameters** — `_normalize_params()` maps model-specific aliases
   to canonical names (`file_path` → `path`, `cmd` → `command`, etc.).
2. **Infer action type** — `_infer_action_type()` maps function name to
   `ActionType`. Falls back to argument-key heuristics when the model emits
   an empty or non-standard function name (observed with `batiai/qwen3.6-35b`).

### `_infer_action_type(name, params) → ActionType | None`

Resolution order:

1. Exact normalised name match (`writefile` → `WRITE_FILE`).
2. Substring match in normalised name (`write` → `WRITE_FILE`).
3. Argument-key heuristics:
   - `command` present → `EXECUTE`
   - `pattern` present → `SEARCH_FILES`
   - `recursive` present → `LIST_DIRECTORY`
   - `content` / `file_content` / `text` present → `WRITE_FILE`
   - `path` / `file_path` present → `READ_FILE`

### `_PARAM_ALIASES`

A dict mapping variant parameter names to canonical names:

```python
{
    "file_path": "path", "filepath": "path", "filename": "path",
    "file_content": "content", "file_contents": "content", "text": "content",
    "file_mode": "mode",
    "directory": "path", "dir": "path", "dir_path": "path",
    "cmd": "command", "shell_command": "command",
    "cwd": "working_dir", "working_directory": "working_dir",
}
```

---

## Layer 2 — Core Layer

### Action Schema

**File:** `src/aicli/core/actions.py`

#### `ActionType` (enum)

```python
class ActionType(str, Enum):
    READ_FILE      = "read_file"
    WRITE_FILE     = "write_file"
    LIST_DIRECTORY = "list_directory"
    EXECUTE        = "execute"
    SEARCH_FILES   = "search_files"
```

#### `WriteMode` (enum)

```python
class WriteMode(str, Enum):
    CREATE    = "create"     # fails if file already exists
    OVERWRITE = "overwrite"  # truncate and write
    APPEND    = "append"     # append to existing content
```

#### `ActionRequest`

```python
@dataclass
class ActionRequest:
    action_type: ActionType
    params: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)
```

Canonical parameter sets per action type:

| Action           | Required params | Optional params                            |
|------------------|-----------------|--------------------------------------------|
| `read_file`      | `path`          | —                                          |
| `write_file`     | `path`,         | —                                          |
|                  | `content`,      |                                            |
|                  | `mode`          |                                            |
| `list_directory` | `path`          | `recursive` (bool, default False)          |
| `execute`        | `command`       | `working_dir`, `timeout` (int, default 30) |
| `search_files`   | `pattern`,      | `type` (glob\|regex, default glob)         |
|                  | `path`          |                                            |

#### `ActionResult`

```python
@dataclass
class ActionResult:
    action_type: ActionType
    success: bool
    data: dict[str, Any]   # action-specific result fields
    error: str | None       # set when success=False
```

`ActionResult.to_context_string()` renders the result as a human-readable string
suitable for feeding back to the model as a user message.

#### `NATIVE_TOOL_SCHEMAS`

A list of OpenAI-compatible function schemas (JSON Schema objects) for all five
action types. Drivers that support native tool calling pass this list verbatim
to the API.

---

### Action Parser

**File:** `src/aicli/core/parser.py`

Used when a driver does not support native tools. Scans model output for
`<aicli_action>` XML blocks and returns `ActionRequest` objects.

#### `parse_action_blocks(text: str) → Generator[ActionRequest, None, None]`

Yields one `ActionRequest` per valid `<aicli_action type="...">...</aicli_action>`
block found in `text`. Blocks with unknown action types or missing required
parameters are silently skipped.

#### `split_text_and_actions(text: str) → tuple[str, list[ActionRequest]]`

Returns `(clean_text, actions)` where `clean_text` has all action blocks stripped.

#### Action block format (system-prompt mode)

```xml
<aicli_action type="write_file">
path: /absolute/path/to/file
mode: overwrite
content:
<<<CONTENT
file content here
CONTENT>>>
</aicli_action>

<aicli_action type="read_file">
path: /absolute/path/to/file
</aicli_action>

<aicli_action type="execute">
command: gnuplot script.gnuplot
working_dir: /optional/dir
timeout: 30
</aicli_action>

<aicli_action type="list_directory">
path: /absolute/path
recursive: true
</aicli_action>

<aicli_action type="search_files">
pattern: *.sql
path: /search/root
type: glob
</aicli_action>
```

The `<<<CONTENT ... CONTENT>>>` heredoc is the only multi-line value; all other
fields are single-line `key: value` pairs.

---

### Executor

**File:** `src/aicli/core/executor.py`

Receives `ActionRequest`, enforces permissions, performs the action, returns
`ActionResult`. **Never communicates with a model or driver.**

#### `Executor(allowed_dirs, allow_exec)`

```python
executor = Executor(
    allowed_dirs=["/home/user/project", "/tmp"],
    allow_exec=True,
)
```

- `allowed_dirs` — list of directory paths. Resolved to absolute paths at
  construction time. Writes outside these dirs raise `PermissionError`.
- `allow_exec` — gates the `execute` action.

#### `executor.execute(req: ActionRequest) → ActionResult`

Dispatches to the appropriate handler. Raises `PermissionError` (a subclass of
`Exception` defined in `executor.py`) for write permission violations. All other
errors are returned as `ActionResult(success=False, error=...)`.

#### Permission rules

| Operation | Rule |
|-----------|------|
| `read_file` | Always permitted — any path. |
| `write_file` | Target must be inside at least one `allowed_dir` after symlink resolution. |
| `list_directory` | Always permitted. |
| `execute` | Only if `allow_exec=True`. |
| `search_files` | Always permitted. |

Path resolution uses `Path.resolve()` which follows symlinks, so a symlink
pointing outside an allowed directory will be blocked for writes.

---

### Session

**File:** `src/aicli/core/session.py`

Maintains the ordered message history for multi-turn conversations.

```python
@dataclass
class Session:
    messages: list[Message]
    system_prompt: str

    def add_user(self, content: str) -> None: ...
    def add_assistant(self, content: str) -> None: ...
    def add_tool_result(self, content: str) -> None:
        # Appended as a "user" role message so the model sees action results.
        ...
    def as_ollama_messages(self) -> list[dict]:
        # Returns messages in Ollama chat API format: [{role, content}, ...]
        ...
```

`add_tool_result()` injects action results as user-role messages. This is how
the agentic loop works: after the executor runs, its output is appended to the
session and the model receives it in the next round.

---

### System Prompts

**File:** `src/aicli/core/system_prompt.py`

Two system prompt builders, selected by `cli.py` based on whether the driver
supports native tools:

#### `build_system_prompt(user_system_prompt="") → str`

Used when `driver.supports_native_tools()` is `False`. Prepends the full
`ACTION_SYSTEM_PROMPT` (which teaches the model the `<aicli_action>` format)
to any user-supplied system prompt.

#### `build_native_tools_system_prompt(user_system_prompt="") → str`

Used when `driver.supports_native_tools()` is `True`. Prepends a brief
`NATIVE_TOOLS_HINT` (which instructs the model to always call tools instead of
refusing) to any user-supplied system prompt.

#### Extending the system prompt

To inject additional context for all sessions, add it to `.aicli.yaml` or pass
it with `--system-prompt-file`. The user system prompt is appended after the
aicli-managed portion — it never replaces it.

---

## Layer 3 — Driver Interface

**File:** `src/aicli/drivers/base.py`

Every driver must subclass `BaseDriver` and implement four abstract methods.

```python
class BaseDriver(ABC):

    @abstractmethod
    def configure(
        self,
        api_base: str,
        api_key: str | None,
        model: str,
        options: dict | None = None,
    ) -> None:
        """Store configuration. Called once before any send()."""

    @abstractmethod
    def send(
        self,
        messages: list[dict],
        system_prompt: str = "",
        stream: bool = True,
    ) -> Generator[ResponseChunk, None, None]:
        """
        Yield ResponseChunk objects.

        - Text chunks: ResponseChunk(text="...", done=False)
        - Terminal chunk: ResponseChunk(done=True, native_tool_calls=[...],
                                        tokens_in=N, tokens_out=M, model="...")
        Always yield exactly one done=True chunk as the last item.
        """

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return list of available model name strings."""

    @abstractmethod
    def supports_native_tools(self) -> bool:
        """Return True if this driver/model supports native function calling."""

    def get_native_tool_schema(self) -> list[dict] | None:
        """Return the tool schema list, or None if not supported."""
        return None
```

### `ResponseChunk` dataclass

```python
@dataclass
class ResponseChunk:
    text: str = ""                              # non-empty for text chunks
    done: bool = False                          # True only for the terminal chunk
    native_tool_calls: list[NativeToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
```

### `NativeToolCall` dataclass

```python
@dataclass
class NativeToolCall:
    name: str       # function name (may be empty or non-standard for some models)
    params: dict    # raw argument dict from the model
    call_id: str = ""
```

---

## Ollama Driver

**File:** `src/aicli/drivers/ollama.py`

### Capability detection

On the first call to `supports_native_tools()`, the driver queries
`POST /api/show` with the model name and inspects the `capabilities` array:

- `"tools"` in capabilities → `supports_native_tools()` returns `True`
- `"thinking"` in capabilities → `_has_thinking_mode()` returns `True`

Both results are cached for the lifetime of the driver instance.

### Thinking mode auto-disable

When `thinking` is in capabilities and the user has not explicitly set `think`
in `options`, the driver adds `"options": {"think": false}` to the request body.
This is necessary because thinking mode re-activates RLHF-trained refusals on
file operations in the qwen3 and glm4 model families.

To override this behaviour (e.g. to force thinking mode), set `think: true`
in the config:

```yaml
# ~/.config/aicli/config.yaml
drivers:
  ollama:
    options:
      think: true
```

### Tool choice

When native tools are enabled, the driver adds `"tool_choice": "required"` to
the request body. This instructs the model to always call a tool rather than
responding with plain text.

### Streaming protocol

The Ollama streaming response is newline-delimited JSON. Each line is a chunk:

```json
{"message": {"role": "assistant", "content": "Hello"}, "done": false}
{"message": {"role": "assistant", "content": " world"}, "done": false}
{"message": {"tool_calls": [{"function": {"name": "write_file", "arguments": {...}}}]}, "done": false}
{"done": true, "prompt_eval_count": 42, "eval_count": 15}
```

Text content and tool calls may appear in any chunk, including the final done chunk.

---

## Driver Registry

**File:** `src/aicli/drivers/registry.py`

```python
from aicli.drivers.registry import get_driver, list_drivers

driver = get_driver("ollama")   # returns OllamaDriver()
names  = list_drivers()         # ["ollama", "gemini", "claude", "openai"]
```

`get_driver(name)` raises `ValueError` for unknown names.

To register a new driver, add it to `_REGISTRY` in `registry.py`:

```python
_REGISTRY: dict[str, type[BaseDriver]] = {
    "ollama":  OllamaDriver,
    "gemini":  GeminiDriver,
    "claude":  ClaudeDriver,
    "openai":  OpenAIDriver,
    "myvendor": MyVendorDriver,   # ← add here
}
```

---

## Config

**File:** `src/aicli/config.py`

### `load_config() → dict`

Loads and deep-merges configurations in order:

1. Built-in defaults (`_DEFAULTS`)
2. `~/.config/aicli/config.yaml`
3. `./.aicli.yaml` (current working directory)

Returns a flat merged dict. Missing keys always fall through to defaults.

### `driver_config(config, driver_name) → dict`

Extracts the `config["drivers"][driver_name]` sub-dict, or `{}` if absent.

### `resolve_api_key(driver_cfg) → str | None`

Reads the API key for a driver: first checks `api_key_env` (environment variable
name), then `api_key` (literal value). Returns `None` if neither is set.

---

## Output Layer

### `Renderer` (`output/renderer.py`)

Handles terminal output for streaming text and action feedback.

```python
renderer = Renderer(markdown=True)   # rich Markdown rendering enabled

# Called during streaming
renderer.stream_chunk("partial text...")
renderer._buffer  # accumulated text so far

# Called when streaming is done
renderer.finalize()   # prints final newline; re-renders with rich if markdown=True

# Action feedback
renderer.print_action_header("write_file", "/tmp/out.txt")
renderer.print_action_result(success=True, message="Done.")

# User confirmation (returns True = approved, False = skipped)
approved = renderer.confirm("Allow write_file?")

# Info/warning/error messages to stderr
renderer.print_info("...")
renderer.print_warning("...")
renderer.print_error("...")
```

### `SessionLogger` (`output/logger.py`)

```python
logger = SessionLogger(log_dir="~/.local/share/aicli/logs", enabled=True)
logger.log("user", "the user's prompt text")
logger.log("assistant", "the model's response text")
logger.log("tool", "action result context string")
logger.close()
```

Log files are written to `<log_dir>/session_<YYYYMMDD_HHMMSS>.log`.

---

## Data Flow — One Full Turn

This traces a single user prompt through the entire system.

```
User types: "Write a gnuplot script to /tmp/sine.gnuplot"

1. cli.main()
   → Session.add_user("Write a gnuplot script ...")
   → Logger.log("user", ...)

2. cli.run_turn() — Round 1
   → session.as_ollama_messages()
     returns [{role: "user", content: "Write a gnuplot script ..."}]
   → driver.send(messages, system_prompt=NATIVE_TOOLS_HINT, stream=True)
     yields ResponseChunk(text="I'll create that file.")
     yields ResponseChunk(text="")
     yields ResponseChunk(done=True, native_tool_calls=[
               NativeToolCall(name="write_file",
                              params={"path": "/tmp/sine.gnuplot", "content": "..."})
           ])
   → renderer.stream_chunk("I'll create that file.")
   → renderer.finalize()
   → Logger.log("assistant", "I'll create that file.")

3. cli.run_turn() — Action dispatch
   → _native_call_to_action_request(NativeToolCall(...))
     → _normalize_params({"path": ..., "content": ...})
     → ActionRequest(WRITE_FILE, {path, content, mode=OVERWRITE})
   → renderer.print_action_header("write_file", "/tmp/sine.gnuplot")
   → renderer.confirm("Allow write_file?")  [if not auto_approve]
   → executor.execute(ActionRequest(WRITE_FILE, ...))
     → _resolve_and_check("/tmp/sine.gnuplot", write=True)
       → Path("/tmp/sine.gnuplot").resolve() → /tmp/sine.gnuplot
       → is /tmp/sine.gnuplot inside /tmp? yes → OK
     → path.write_text(content)
     → ActionResult(WRITE_FILE, success=True, data={"path": "/tmp/sine.gnuplot"})
   → renderer.print_action_result(True, "Done.")
   → result.to_context_string()
     → "[write_file result]: File written successfully to /tmp/sine.gnuplot"
   → session.add_tool_result("[write_file result]: ...")
   → Logger.log("tool", ...)

4. cli.run_turn() — Round 2
   → session.as_ollama_messages() now has 3 messages:
       [{role: "user",      content: "Write a gnuplot script..."},
        {role: "assistant", content: "I'll create that file."},
        {role: "user",      content: "[write_file result]: ..."}]
   → driver.send(messages, ...)
     yields ResponseChunk(text="The gnuplot script has been written.")
     yields ResponseChunk(done=True, native_tool_calls=[])  ← no more actions
   → renderer streams + finalises
   → session.add_assistant("The gnuplot script has been written.")
   → No actions → break out of round loop
```

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Write outside allowed dirs | `executor.execute()` raises `PermissionError`; caught in `run_turn()`; error shown to user; action skipped |
| Execute without `--allow-exec` | `ActionResult(success=False, error="Command execution denied: ...")` |
| Model refused to call a tool | Done chunk has `native_tool_calls=[]`; `split_text_and_actions()` is tried on text; if no XML blocks found, turn ends normally |
| Ollama server unreachable | `httpx` raises; propagates to CLI; Python traceback |
| Driver not implemented | `get_driver("gemini")` succeeds; first `configure()` or `send()` call raises `NotImplementedError` |
| Action round limit reached | Warning printed; loop exits after `max_action_rounds=10` rounds |
