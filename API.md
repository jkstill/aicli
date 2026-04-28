# aicli Internal API Reference

This document describes the internal layers and contracts connecting the CLI
frontend, the V2 planner/executor core, and the vendor drivers. It is intended
for contributors, driver authors, and anyone extending aicli's behaviour.

For adding a new LLM provider see [ADD-LLM.md](ADD-LLM.md).

---

## Table of Contents

- [Architectural Overview](#architectural-overview)
- [Layer 1 — CLI Frontend (`cli.py`)](#layer-1--cli-frontend)
- [Layer 2 — V2 Planner/Executor Core](#layer-2--v2-plannerexecutor-core)
  - [Plan Parser (`core/plan_parser.py`)](#plan-parser)
  - [Result Store (`core/result_store.py`)](#result-store)
  - [Planner (`core/planner.py`)](#planner)
  - [Orchestrator (`core/orchestrator.py`)](#orchestrator)
  - [Executor (`core/executor.py`)](#executor)
  - [Action Schema (`core/actions.py`)](#action-schema)
  - [System Prompts (`core/system_prompts/`)](#system-prompts)
- [Layer 3 — Driver Interface (`drivers/base.py`)](#layer-3--driver-interface)
- [Ollama Driver (`drivers/ollama.py`)](#ollama-driver)
- [Driver Registry (`drivers/registry.py`)](#driver-registry)
- [Config (`config.py`)](#config)
- [Output Layer (`output/`)](#output-layer)
- [Data Flow — One Full Task](#data-flow--one-full-task)
- [Error Handling](#error-handling)

---

## Architectural Overview

```
┌───────────────────────────────────────────────────────┐
│  cli.py  (main, run_task)                             │
│  Owns: renderer, logger, driver instances             │
└──────────────────────┬────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │  Planner                │  sends task to LLM, gets plan text
          │  plan_parser.py         │  text → list[PlanStep]
          │  result_store.py        │  step outputs + {RESULT_OF_STEP_N}
          │  orchestrator.py        │  step execution loop
          └────────────┬────────────┘
                       │
          ┌────────────┴────────────┐
          │  executor.py            │  READFILE / WRITEFILE / LISTDIR / EXEC
          │  actions.py             │  ActionRequest / ActionResult / ActionType
          └─────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │  Driver (analysis)      │  PROMPT / GENCODE dispatched here
          │  base.py                │  BaseDriver ABC
          │  ollama.py              │  Ollama REST implementation
          │  registry.py            │  name → driver class
          └─────────────────────────┘
```

**Key invariants:**

- The executor never calls the driver. The driver never calls the executor.
- The LLM never touches the filesystem; only the orchestrator/executor does.
- The planner and the analysis driver may be different model instances.

---

## Layer 1 — CLI Frontend

**File:** `src/aicli/cli.py`

### `main()` — Click command

Entry point. Responsibilities:

1. Load config and parse CLI flags.
2. Resolve `driver/model-name` for the planner driver.
3. Resolve `--analysis-model` (falls back to planner driver if not specified).
4. Load the planner system prompt (built-in or `--system-prompt-file`).
5. Dispatch to pipe / file / REPL mode.

### `run_task(task, planner_driver, analysis_driver, ...)`

Executes one full user task through the planner/executor pipeline:

```
1. Planner.get_plan(task)         → plan text (streamed if --verbose)
2. parse_plan(plan_text)          → list[PlanStep]
3. renderer.print_plan(steps)     → show parsed plan to user
4. if --dry-run: return
5. Orchestrator.run(steps)        → execute each step
```

### `_setup_driver(driver_name, model_name, config, ...)`

Instantiates, configures, and returns a driver. Used for both the planner
driver and the (optional) separate analysis driver.

---

## Layer 2 — V2 Planner/Executor Core

### Plan Parser

**File:** `src/aicli/core/plan_parser.py`

Converts raw LLM plan text into an ordered list of `PlanStep` objects.

#### `PlanStep` dataclass

```python
@dataclass
class PlanStep:
    number: int      # 1-indexed step number
    keyword: str     # uppercase: READFILE, WRITEFILE, LISTDIR, EXEC, PROMPT, GENCODE
    arg: str         # text on the keyword line (command, path, or language)
    body: str        # multi-line body following the keyword line
    save_path: str   # GENCODE only: extracted from SAVEAS: line
```

#### `parse_plan(text: str) → list[PlanStep]`

Parses an LLM response into steps. Tolerates common model output variations:

| Variation | Example | Handled by |
|-----------|---------|------------|
| Standard format | `READFILE: cat /path` | Default |
| Bare path (no command) | `READFILE: /path/to/file` | Normalized to `cat /path/to/file` |
| Key=value arg | `READFILE file="/path"` | Regex extraction |
| Backtick-wrapped line | `` `READFILE: /path` `` | Backtick strip |
| Step-numbered prefix | `Step 1: READFILE: /path` | Regex prefix group |
| Outer code fence | ` ```\nREADFILE...\n``` ` | `_strip_outer_fence()` |
| Lowercase keywords | `readfile: /path` | Case-insensitive regex |

**READFILE normalization:** If the arg starts with `/` or `~` and contains no
spaces, it is treated as a bare path and prefixed with `cat`. Key=value formats
(`file=`, `path=`, `filename=`) are also normalized to `cat <path>`.

**GENCODE:** The `SAVEAS: <path>` line within the GENCODE body is extracted into
`step.save_path` and removed from `step.body`. A fallback also checks for
`output=` in the keyword arg line.

**WRITEFILE:** If the arg matches `file=<path>` or `path=<path>`, only the path
is kept as `arg`. The body (possibly empty) becomes the write content.

#### `KEYWORDS`

```python
KEYWORDS = frozenset(["READFILE", "WRITEFILE", "LISTDIR", "EXEC", "PROMPT", "GENCODE"])
```

---

### Result Store

**File:** `src/aicli/core/result_store.py`

Stores step outputs keyed by step number and substitutes placeholder references.

#### `ResultStore`

```python
store = ResultStore()
store.store(1, "CSV data content here")
store.store(2, "Analysis text from LLM")

store.get(1)     # → "CSV data content here"
store.latest()   # → "Analysis text from LLM" (most recently stored)

text = "Based on {RESULT_OF_STEP_1} and {RESULT_OF_PREVIOUS_STEP}"
store.substitute(text)
# → "Based on CSV data content here and Analysis text from LLM"
```

#### Substitution patterns

| Pattern | Replaced with |
|---------|--------------|
| `{RESULT_OF_STEP_N}` | Output of step N (1-indexed) |
| `{RESULT_OF_PREVIOUS_STEP}` | Output of the most recently stored step |

Both patterns are case-insensitive. If a referenced step number has no stored
result, the placeholder is replaced with a `[Result of step N not available]`
string.

---

### Planner

**File:** `src/aicli/core/planner.py`

Sends the user's task to the LLM with the planner system prompt and returns the
raw plan text.

#### `load_system_prompt(override_path=None) → str`

Loads the planner system prompt. With no argument, reads the built-in
`core/system_prompts/default.md`. Pass a path to override.

#### `Planner(driver, system_prompt)`

```python
planner = Planner(driver=ollama_driver, system_prompt=load_system_prompt())
plan_text = planner.get_plan(
    task="Analyze /tmp/data.csv and write a report to /tmp/report.md",
    stream_callback=renderer.stream_chunk,  # optional: stream as it arrives
)
```

`get_plan()` wraps the task in a directive prefix to improve model compliance:

```
Produce a step plan using ONLY step blocks (READFILE, WRITEFILE, ...). No prose.

TASK: <user task>
```

The driver is called with `use_tools=False` — no native tool schemas are
included in the request.

---

### Orchestrator

**File:** `src/aicli/core/orchestrator.py`

Executes a list of `PlanStep` objects sequentially, managing the `ResultStore`
and dispatching steps to the appropriate handler.

#### `Orchestrator(analysis_driver, allowed_dirs, allow_exec, auto_approve, dry_run, verbose, renderer, exec_timeout)`

```python
orch = Orchestrator(
    analysis_driver=driver,
    allowed_dirs=["/tmp", "/home/user/reports"],
    allow_exec=True,
    auto_approve=True,
    dry_run=False,
    verbose=False,
    renderer=renderer,
    exec_timeout=300,
)
final_output = orch.run(steps)
```

#### `Orchestrator.run(steps: list[PlanStep]) → str`

Iterates through all steps. Returns the text output of the last `PROMPT` step
(if any) — suitable for display or logging.

#### Step handlers

| Step | Handler | Notes |
|------|---------|-------|
| READFILE | `_exec_readfile` | `subprocess.run(arg, shell=True)` — no `--allow-exec` needed |
| WRITEFILE | `_exec_writefile` | Uses `Executor._write_file`; path must be in `allowed_dirs` |
| LISTDIR | `_exec_listdir` | Uses `Executor._list_directory` |
| EXEC | `_exec_exec` | Uses `Executor._execute`; requires `allow_exec=True` |
| PROMPT | `_exec_prompt` | Dispatches to `analysis_driver.send(use_tools=False)` |
| GENCODE | `_exec_gencode` | Dispatches to LLM with code-generation system prompt; strips fences; writes to `save_path` |

#### Auto-injection

If a `PROMPT` step's content contains no `{RESULT_OF_STEP_N}` references,
the most recent stored result is automatically appended as `\n\nData:\n<result>`.

If a `WRITEFILE` step's body is empty, the most recent stored result is used
as the write content.

This compensates for models that generate steps without explicit result
references.

#### Confirmation

For `WRITEFILE`, `EXEC`, and `GENCODE` steps, a Y/n confirmation is shown
unless `auto_approve=True`. The user can skip individual steps.

---

### Executor

**File:** `src/aicli/core/executor.py`

Low-level file I/O and shell execution engine. Used by the orchestrator for
WRITEFILE, LISTDIR, and EXEC steps. **Never communicates with a model or driver.**

#### `Executor(allowed_dirs, allow_exec)`

```python
executor = Executor(
    allowed_dirs=["/home/user/project", "/tmp"],
    allow_exec=True,
)
result = executor.execute(ActionRequest(...))
```

#### Permission rules

| Operation | Rule |
|-----------|------|
| Read | Always permitted — any path. |
| Write | Target must resolve to inside at least one `allowed_dir`. |
| List | Always permitted. |
| Execute | Only if `allow_exec=True`. |
| Search | Always permitted. |

Path resolution uses `Path.resolve()` (follows symlinks), so a symlink pointing
outside an allowed directory is blocked for writes.

---

### Action Schema

**File:** `src/aicli/core/actions.py`

Defines the action vocabulary used internally by the executor.

#### `ActionType` (enum)

```python
class ActionType(str, Enum):
    READ_FILE      = "read_file"
    WRITE_FILE     = "write_file"
    LIST_DIRECTORY = "list_directory"
    EXECUTE        = "execute"
    SEARCH_FILES   = "search_files"
```

#### `ActionRequest` / `ActionResult`

```python
@dataclass
class ActionRequest:
    action_type: ActionType
    params: dict[str, Any]

@dataclass
class ActionResult:
    action_type: ActionType
    success: bool
    data: dict[str, Any]
    error: str | None
```

`ActionResult.to_context_string()` renders the result as human-readable text.

---

### System Prompts

**Directory:** `src/aicli/core/system_prompts/`

#### `default.md`

The built-in V2 planner system prompt. Instructs the LLM to respond with
tagged step blocks only. Includes a worked example to improve model compliance.

Key rules enforced by the prompt:

- Respond with ONLY step blocks — no prose or explanations
- Always use absolute paths
- For GENCODE, always include a SAVEAS: line
- Use `{RESULT_OF_STEP_N}` to reference prior step output
- Keep each step focused on one action

To override the system prompt for a session:

```bash
aicli --system-prompt-file my-custom-planner.md ...
```

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
        use_tools: bool = True,
    ) -> Generator[ResponseChunk, None, None]:
        """
        Yield ResponseChunk objects.

        Text chunks:    ResponseChunk(text="...", done=False)
        Terminal chunk: ResponseChunk(done=True, native_tool_calls=[...],
                                      tokens_in=N, tokens_out=M)

        Always yield exactly one done=True chunk as the last item.

        use_tools=False: suppress native tool schemas from the request.
        This is always set by the planner and analysis calls in V2 mode —
        V2 does not use tool calling.
        """

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return list of available model name strings."""

    @abstractmethod
    def supports_native_tools(self) -> bool:
        """Return True if this driver/model supports native function calling."""

    def get_native_tool_schema(self) -> list[dict] | None:
        return None
```

### `ResponseChunk` dataclass

```python
@dataclass
class ResponseChunk:
    text: str = ""
    done: bool = False
    native_tool_calls: list[NativeToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""
```

### `NativeToolCall` dataclass

```python
@dataclass
class NativeToolCall:
    name: str
    params: dict
    call_id: str = ""
```

### The `use_tools` parameter

In V2, the planner and orchestrator always call `driver.send(..., use_tools=False)`.
This suppresses native tool schemas so the model responds with plain text (the
step plan or the PROMPT/GENCODE response). Drivers must respect this flag.

---

## Ollama Driver

**File:** `src/aicli/drivers/ollama.py`

### Capability detection

On the first `supports_native_tools()` call, the driver queries `POST /api/show`
for the model and inspects the `capabilities` array:

- `"tools"` → native tools supported
- `"thinking"` → thinking/reasoning mode available

Both are cached for the driver's lifetime.

### `use_tools=False` behaviour

When `use_tools=False` is passed to `send()`, the driver skips adding
`"tools"` and `"tool_choice"` to the request body, even for capable models.
The model responds with plain text.

### Thinking mode auto-disable

When `thinking` is in capabilities and `use_tools=True` (legacy/V1 mode),
the driver adds `"options": {"think": false}` unless the user has set `think`
in config. Thinking mode re-activates RLHF refusals on file operations.

### Streaming protocol

Newline-delimited JSON. Each line is a chunk:

```json
{"message": {"role": "assistant", "content": "Hello"}, "done": false}
{"message": {"role": "assistant", "content": " world"}, "done": false}
{"done": true, "prompt_eval_count": 42, "eval_count": 15}
```

---

## Driver Registry

**File:** `src/aicli/drivers/registry.py`

```python
from aicli.drivers.registry import get_driver, list_drivers

driver = get_driver("ollama")    # → OllamaDriver()
names  = list_drivers()          # → ["ollama", "gemini", "claude", "openai"]
```

`get_driver(name)` raises `ValueError` for unknown names.

To add a driver, import it and add it to `_REGISTRY`:

```python
_REGISTRY: dict[str, type[BaseDriver]] = {
    "ollama":  OllamaDriver,
    "myvendor": MyVendorDriver,   # ← add here
}
```

---

## Config

**File:** `src/aicli/config.py`

### `load_config() → dict`

Deep-merges in order: built-in defaults → `~/.config/aicli/config.yaml` →
`./.aicli.yaml`.

### `driver_config(config, driver_name) → dict`

Returns `config["drivers"][driver_name]` or `{}`.

### `resolve_api_key(driver_cfg) → str | None`

Reads key from `api_key_env` (env var name) or `api_key` (literal).

### `filter_models(models, config) → list[str]`

Removes models matching `config["model_exclusions"]` patterns (fnmatch,
case-insensitive).

---

## Output Layer

### `Renderer` (`output/renderer.py`)

```python
renderer = Renderer(markdown=True)

# Streaming
renderer.stream_chunk("partial text...")
renderer.finalize()

# Plan display (V2)
renderer.print_plan(steps)       # display step list before execution

# Confirmations and status
renderer.confirm("Allow WRITEFILE?")   # → True | False
renderer.print_info("...")
renderer.print_warning("...")
renderer.print_error("...")
renderer.print_action_header("WRITEFILE", "/tmp/report.md")
renderer.print_action_result(success=True, message="Done.")
```

### `SessionLogger` (`output/logger.py`)

```python
logger = SessionLogger(log_dir="~/.local/share/aicli/logs", enabled=True)
logger.log("user", "task text")
logger.log("assistant", "plan text")
logger.log("tool", "step results")
logger.close()
```

Logs to `<log_dir>/session_<YYYYMMDD_HHMMSS>.log`.

---

## Data Flow — One Full Task

```
User: "Read /tmp/data.csv, analyze for anomalies, write to /tmp/report.md"

1. cli.run_task()
   → load_system_prompt()  → reads core/system_prompts/default.md
   → Planner.get_plan(task)
       → messages = [{"role": "user", "content": "Produce a step plan...\nTASK: ..."}]
       → driver.send(messages, system_prompt=planner_prompt, use_tools=False)
         yields ResponseChunk(text="READFILE: cat /tmp/data.csv\n") ...
         yields ResponseChunk(text="PROMPT: Analyze for anomalies...\n") ...
         yields ResponseChunk(done=True)
       → plan_text = "READFILE: cat /tmp/data.csv\nPROMPT: ..."

2. parse_plan(plan_text)
   → steps = [
       PlanStep(1, "READFILE", "cat /tmp/data.csv", ""),
       PlanStep(2, "PROMPT", "Analyze for anomalies", ""),
       PlanStep(3, "WRITEFILE", "/tmp/report.md", ""),
     ]

3. renderer.print_plan(steps)
   → shows numbered step list to user

4. Orchestrator.run(steps)
   →
   Step 1: READFILE
     subprocess.run("cat /tmp/data.csv", shell=True)
     store.store(1, "Date,Value,Status\n2024-01-01,100,OK\n...")

   Step 2: PROMPT
     auto-inject: prompt += "\n\nData:\n" + store.latest()
     analysis_driver.send([{"role": "user", "content": expanded_prompt}],
                           use_tools=False)
     yields LLM analysis text...
     store.store(2, "## Anomaly Report\n...")
     renderer.stream_chunk("## Anomaly Report\n...")
     renderer.finalize()

   Step 3: WRITEFILE
     body is empty → auto-inject: content = store.latest()
     Executor._write_file(path="/tmp/report.md", content="## Anomaly Report\n...")
     store.store(3, "Written to /tmp/report.md")
```

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| Write outside allowed dirs | `executor.execute()` raises `PermissionError`; caught in orchestrator; step marked failed; stored as `[Step N failed: ...]` |
| EXEC without `--allow-exec` | `ActionResult(success=False, error="Command execution denied")` |
| GENCODE missing SAVEAS | `StepResult(success=False, error="GENCODE step is missing a SAVEAS: line.")` |
| No plan steps parsed | Warning printed; raw response shown; task aborted |
| Ollama server unreachable | `httpx` exception propagates to CLI; Python traceback |
| Driver not implemented | `configure()` or `send()` raises `NotImplementedError` |
| Step failure | Error printed; step result stored as failure text; execution continues to next step |
