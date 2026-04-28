# aicli v2 — Universal Agentic CLI for Large Language Models

Disclaimer: 

At the time of this writing, open weight models such as those available on Ollama are not yet capable of reliably following the step-based plan format required by aicli v2. 

The planner/executor architecture is designed to be robust even with smaller models, but the quality of the generated plans can vary significantly. 

Or, there may be no plan at all. This is a limitation of the current state of open models, not the framework itself.

I will check the capabilities of new models with this toolkit in 12 months, to see if the models have improved to the point where they can be used effectively with aicli v2.

aicli is a command-line tool that gives any LLM agentic capabilities: reading files,
writing files, listing directories, executing shell commands, and generating code.
Switch between Ollama (local), Gemini, Claude, or OpenAI with a single flag. Your
prompts stay the same regardless of which model runs them.

**v2 introduces the planner/executor model.** Instead of asking the LLM to
*perform* actions directly, aicli v2 asks it to *plan* a sequence of tagged steps.
The framework executes the steps itself. This makes the system reliable with small
local models that struggle with direct action protocols.

```
Architecture (DBI/DBD pattern):

  User task
      │
      ▼
  aicli (CLI)                ← argument parsing, display, confirmation prompts
      │
      ▼
  Planner                    ← sends task + system prompt to LLM, gets step plan
      │
      ▼
  Plan Parser                ← splits LLM response into typed steps
      │
      ▼
  Orchestrator               ← executes steps sequentially
      │           │
      │           ▼
      │       READFILE / WRITEFILE / LISTDIR / EXEC
      │           ↑ framework executes these directly
      │
      ▼
  Analysis driver            ← PROMPT / GENCODE steps dispatched to LLM
      │
      ▼
  Vendor driver              ← ollama | gemini | claude | openai
      │
      ▼
  LLM provider               ← local Ollama / cloud API
```

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Step Keywords](#step-keywords)
- [Input Modes](#input-modes)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Permission Model](#permission-model)
- [Multi-Model Pipelines](#multi-model-pipelines)
- [Model Selection](#model-selection)
- [Model Compatibility](#model-compatibility)
- [Diagnosing Hangs](#diagnosing-hangs)
- [Examples](#examples)
- [Testing](#testing)
- [Project Layout](#project-layout)

---

## Installation

**Requirements:** Python 3.11+

```bash
# From the project root — editable install (recommended for development)
pip install -e .
```

Dependencies installed automatically: `httpx`, `pyyaml`, `rich`, `click`.

---

## Quick Start

```bash
# Analyze a data file and write a report (dry-run to see the plan first)
echo "Read /tmp/data.csv, analyze it for anomalies, and write a report to /tmp/report.md" | \
  aicli --model ollama/qwen3.5 \
        --include-directories /tmp \
        --dry-run

# Run the same task for real
echo "Read /tmp/data.csv, analyze it for anomalies, and write a report to /tmp/report.md" | \
  aicli --model ollama/qwen3.5 \
        --include-directories /tmp \
        --auto-approve

# Use one model for planning and a larger one for analysis
aicli --model ollama/qwen3.5 \
      --analysis-model ollama/batiai/qwen3.6-35b:q4 \
      --prompt-file prompts/analyze-sar.md \
      --include-directories ~/oracle-hc \
      --auto-approve

# Interactive REPL
aicli --model ollama/qwen3.5 --include-directories ~/project
```

---

## How It Works

### 1. The LLM produces a plan

When you give aicli a task, it sends the task to the LLM along with a system
prompt that instructs the model to respond with a sequence of tagged steps —
not prose, not code, not explanations. Just steps.

```
READFILE: head -50 /home/user/data/sar.csv
PROMPT: Identify the columns that show memory pressure in this data:
{RESULT_OF_STEP_1}
GENCODE: gnuplot
SAVEAS: /home/user/charts/memory.gnuplot
Create a gnuplot script that plots the memory pressure columns.
Use /home/user/data/sar.csv as input. Output PNG at 1200x800.
EXEC: gnuplot /home/user/charts/memory.gnuplot
PROMPT: Write a markdown analysis of the memory pressure in this server.
Reference the chart image generated in the previous step.
{RESULT_OF_STEP_2}
WRITEFILE: /home/user/reports/memory-analysis.md
{RESULT_OF_STEP_5}
```

### 2. The framework executes the plan

aicli processes each step in order:

| Step | Who executes | What happens |
|------|-------------|--------------|
| `READFILE` | Framework | Runs the shell command; stores stdout |
| `LISTDIR` | Framework | Lists the directory; stores the listing |
| `EXEC` | Framework | Runs the command; stores stdout/stderr |
| `WRITEFILE` | Framework | Writes content to the file |
| `PROMPT` | LLM (analysis driver) | Sends prompt to LLM; stores the response |
| `GENCODE` | LLM → Framework | LLM generates code; framework strips fences and saves it |

### 3. Results flow between steps

Use `{RESULT_OF_STEP_N}` (1-indexed) in any step body to reference the output
of a prior step. Use `{RESULT_OF_PREVIOUS_STEP}` for the most recent result.

If a step doesn't include an explicit reference, aicli automatically injects
the most recent result — this handles the common case where the model forgets
to add references.

---

## Step Keywords

| Keyword | Argument | Body | Description |
|---------|----------|------|-------------|
| `READFILE` | Shell command | — | Run a read command (`cat`, `head`, `grep`, …) and store its output |
| `WRITEFILE` | Target path | Content to write (may include `{RESULT_OF_STEP_N}`) | Write content to a file |
| `LISTDIR` | Directory path | — | List directory contents |
| `EXEC` | Shell command | — | Execute a command (requires `--allow-exec`) |
| `PROMPT` | Analytical prompt | Additional prompt lines | Send to LLM; store the response |
| `GENCODE` | Language name | `SAVEAS: <path>` + generation instructions | LLM generates code; framework saves it |

---

## Input Modes

### 1. Pipe mode

```bash
echo "Read /var/log/syslog last 100 lines and summarize errors" | \
  aicli --model ollama/qwen3.5 --include-directories ~/reports -y
```

Stdin is read as the task prompt. Exits after one task.

### 2. File mode

```bash
aicli --prompt-file prompts/analyze-sar.md \
      --model ollama/qwen3.5 \
      --include-directories ~/oracle-hc \
      --allow-exec -y
```

The file contents are the task prompt. Exits after one task.

### 3. Interactive REPL

If neither pipe nor file mode applies, aicli drops into an interactive session.
Each prompt you type is one task:

```
$ aicli --model ollama/qwen3.5 --include-directories ~/project

aicli v2 | planner=ollama/qwen3.5 ...
Interactive mode — type 'exit' or Ctrl-D to quit.

aicli> What files are in ~/project?
aicli> Read the README.md and summarize it.
aicli> exit
```

---

## CLI Reference

```
Usage: aicli [OPTIONS]

  aicli v2 — planner/executor CLI for large language models.

Options:
  -m, --model TEXT                Planner model as driver/model-name
                                  (e.g. ollama/qwen3.5)
      --analysis-model TEXT       Analysis model for PROMPT/GENCODE steps
                                  (default: same as --model)
  -f, --prompt-file PATH          Read task prompt from file
      --system-prompt-file PATH   Override the built-in planner system prompt
  -d, --include-directories TEXT  Comma-separated directories the framework
                                  may write to
      --allow-exec                Permit shell command execution (EXEC steps)
  -y, --auto-approve              Skip confirmation prompts for writes
      --dry-run                   Show parsed plan without executing steps
      --verbose                   Show substituted prompts and step details
      --no-markdown               Disable rich Markdown rendering
      --list-models               List available models for the driver and exit
      --api-base TEXT             Override driver API base URL
      --api-key TEXT              Override driver API key
      --log-sessions              Write session log to file
      --on-error [continue|abort|ask]
                                  What to do on step failure (default: ask)
      --trace FILE                Write timing trace to FILE; flushed
                                  immediately so the file is readable even
                                  if the process is killed mid-run
      --stream-timeout FLOAT      Seconds to wait for the next streamed token
                                  before aborting (default: 120; 0 = no limit)
  -h, --help                      Show this message and exit.
```

### Option details

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | from config | `driver/model-name`. The prefix selects the driver. |
| `--analysis-model` | same as `--model` | Separate model for PROMPT and GENCODE steps. |
| `--include-directories` | none | Comma-separated paths the framework may write to. Reads are unrestricted. |
| `--allow-exec` | off | Enable EXEC steps. Off by default for safety. |
| `--auto-approve` / `-y` | off | Skip all Y/n confirmation prompts. |
| `--dry-run` | off | Display the parsed plan without executing any step. |
| `--verbose` | off | Show full substituted prompts and step details while executing. |
| `--no-markdown` | off | Print raw text instead of rich-rendered Markdown. |
| `--log-sessions` | off | Append full logs to `~/.local/share/aicli/logs/session_<ts>.log`. |
| `--on-error` | `ask` | What to do when a step fails: `continue`, `abort`, or `ask`. |
| `--system-prompt-file` | built-in | Replace the planner system prompt entirely. |
| `--trace FILE` | off | Write a timing trace to FILE (see [Diagnosing Hangs](#diagnosing-hangs)). |
| `--stream-timeout` | `120` | Seconds to wait for the next streamed token. Use `0` for no limit. |

---

## Configuration

aicli loads configuration from two files (later wins):

1. `~/.config/aicli/config.yaml` — global user config
2. `.aicli.yaml` — per-project config (current working directory)

### Full example config

```yaml
# ~/.config/aicli/config.yaml

model: ollama/qwen3.5
analysis_model: null          # null = use same as model
output_format: markdown       # markdown | plain
confirm_actions: true
log_sessions: false
log_dir: ~/.local/share/aicli/logs
exec_timeout: 300             # seconds, for EXEC steps

drivers:
  ollama:
    api_base: http://192.168.1.53:11434
  gemini:
    api_key_env: GEMINI_API_KEY
  claude:
    api_key_env: ANTHROPIC_API_KEY
  openai:
    api_key_env: OPENAI_API_KEY

# Models hidden from --list-models (fnmatch patterns, case-insensitive)
model_exclusions:
  - "*embed*"    # embedding models
  - "llama3:*"   # original llama3 — poor instruction following
```

### Per-project override

```yaml
# .aicli.yaml  (project root)
model: ollama/qwen3-coder:30b
drivers:
  ollama:
    api_base: http://localhost:11434
```

---

## Permission Model

```
Reads:    always allowed — any path
Writes:   only paths inside --include-directories
Execute:  only if --allow-exec is set
Approval: confirmation prompt per write/exec step, unless -y
```

READFILE (shell read commands) and LISTDIR are always permitted.
WRITEFILE and GENCODE (which write the generated code file) require the target
path to be inside an `--include-directories` directory.
EXEC requires `--allow-exec`.

```bash
# /tmp is the only writable directory
aicli --include-directories /tmp

# Multiple directories
aicli --include-directories ~/oracle-hc,/tmp

# Subdirectories are included
aicli --include-directories ~/oracle-hc
# → writes to ~/oracle-hc/charts/foo.gnuplot are OK
# → writes to /etc/passwd are denied
```

---

## Multi-Model Pipelines

Because the planner step and the analysis steps (PROMPT, GENCODE) use the driver
independently, you can use different models for each role:

```bash
# Fast small model for planning, large model for analysis
aicli --model ollama/qwen3.5 \
      --analysis-model ollama/batiai/qwen3.6-35b:q4 \
      --prompt-file prompts/full-hc-report.md \
      --include-directories ~/oracle-hc \
      --allow-exec -y

# Plan locally with Ollama, analyze with the Claude API
aicli --model ollama/qwen3.5 \
      --analysis-model claude/claude-sonnet-4-6 \
      --prompt-file prompts/analyze-logs.md \
      --include-directories ~/reports -y
```

The planning model only needs to produce a structured step list (a simpler task).
The analysis model handles the substantive reasoning in PROMPT and GENCODE steps.

---

## Model Selection

Models are specified as `driver/model-name`:

```bash
aicli --model ollama/qwen3.5
aicli --model ollama/qwen3-coder:30b
aicli --model ollama/batiai/qwen3.6-35b:q4
aicli --model gemini/gemini-2.5-flash        # Phase 3
aicli --model claude/claude-sonnet-4-6       # Phase 3
aicli --model openai/gpt-4o                  # Phase 3
```

### Listing available models

```bash
aicli --model ollama --list-models
aicli --model ollama --list-models --api-base http://192.168.1.53:11434
```

---

## Model Compatibility

In v2, the LLM only needs to produce a structured text plan — no tool calling,
no action block schemas. Any model that can follow formatting instructions works.

### Tested models on Ollama

| Model | Plan quality | Notes |
|-------|-------------|-------|
| `qwen3.5:latest` | Good | Recommended for planning. Standard step format, handles result refs occasionally. |
| `qwen3-coder:30b` | Good | Strong at code generation tasks. |
| `glm-4.7-flash:latest` | Variable | Works well as **analysis model**; plan output format is inconsistent (sometimes key=value, sometimes flowchart syntax). |
| `batiai/qwen3.6-35b:q4` | Good | Good analysis quality. Slow on planning, excellent for PROMPT steps. |
| `qwen3.5:9b` | Fair | Smaller context; works for simple tasks. |

**Recommendation:** Use `qwen3.5` or `qwen3-coder:30b` as the planner model.
Use `glm-4.7-flash` or `batiai/qwen3.6-35b` as the analysis model for PROMPT/GENCODE steps.

### Format variations

The plan parser tolerates common model output variations:

- `READFILE: /path` (bare path) → normalized to `cat /path`
- `READFILE file="/path"` (key=value) → normalized
- `EXEC command="..."` (key=value) → normalized
- `` `READFILE /path` `` (backtick-wrapped) → stripped
- `Step 1: READFILE: ...` (numbered prefix) → handled
- Plan wrapped in ` ``` ` code fence → stripped

---

## Diagnosing Hangs

Large or thinking-mode models (e.g. `batiai/qwen3.6-35b:q3`) can be silent for
many minutes before emitting a single token. Use `--trace` to see exactly where
time is being spent and `--stream-timeout` to set an upper bound.

### Enable tracing

```bash
echo "my task" | aicli \
  --model ollama/batiai/qwen3.6-35b:q3 \
  --include-directories /tmp \
  --trace /tmp/aicli.trace \
  --stream-timeout 300 \
  -y
```

In a second terminal, tail the trace file while the run is in progress:

```bash
tail -f /tmp/aicli.trace
```

### Reading the trace

Each line is `[timestamp] [+elapsed_seconds] EVENT  details`:

```
[2026-04-28T09:01:00] [+    0.001s] CLI_START                      model=None stream_timeout=300.0s
[2026-04-28T09:01:00] [+    0.042s] CAPABILITY_PROBE_START         model=batiai/qwen3.6-35b:q3
[2026-04-28T09:01:00] [+    0.089s] CAPABILITY_PROBE_DONE          model=batiai/qwen3.6-35b:q3 tools=False thinking=True
[2026-04-28T09:01:00] [+    0.091s] TASK_START                     mode=pipe task_len=58
[2026-04-28T09:01:00] [+    0.092s] PLAN_START                     task_len=58
[2026-04-28T09:01:00] [+    0.094s] STREAM_CONNECT                 model=batiai/qwen3.6-35b:q3 read_timeout=300.0s
[2026-04-28T09:15:42] [+  882.341s] STREAM_FIRST_CHUNK             model=batiai/qwen3.6-35b:q3
[2026-04-28T09:15:42] [+  882.342s] PLAN_FIRST_TOKEN
[2026-04-28T09:15:43] [+  883.104s] PLAN_DONE                      response_len=312
[2026-04-28T09:15:43] [+  883.105s] PLAN_EXEC_START                total_steps=3
[2026-04-28T09:15:43] [+  883.106s] STEP_START                     step=1/3 keyword=READFILE arg='cat /tmp/data.csv'
[2026-04-28T09:15:43] [+  883.159s] STEP_DONE                      step=1 keyword=READFILE success=True output_len=4096
[2026-04-28T09:15:43] [+  883.160s] STEP_START                     step=2/3 keyword=PROMPT arg='Analyze the data'
[2026-04-28T09:15:43] [+  883.161s] STEP_LLM_REQUEST               step=2 keyword=PROMPT prompt_len=4210
[2026-04-28T09:15:43] [+  883.162s] STREAM_CONNECT                 model=batiai/qwen3.6-35b:q3 read_timeout=300.0s
[2026-04-28T09:16:04] [+  904.003s] STREAM_FIRST_CHUNK             model=batiai/qwen3.6-35b:q3
[2026-04-28T09:16:04] [+  904.004s] STEP_FIRST_TOKEN               step=2 keyword=PROMPT
[2026-04-28T09:16:09] [+  909.872s] STREAM_DONE                    model=batiai/qwen3.6-35b:q3 tokens_in=512 tokens_out=1024
[2026-04-28T09:16:09] [+  909.873s] STEP_LLM_DONE                  step=2 keyword=PROMPT response_len=3812
```

### Key events and what they mean

| Event | What it tells you |
|-------|------------------|
| `STREAM_CONNECT` | HTTP request sent to Ollama. The gap to `STREAM_FIRST_CHUNK` is time-to-first-token (TTFT). |
| `STREAM_FIRST_CHUNK` | First token received — model is no longer "thinking". |
| `PLAN_FIRST_TOKEN` | First planning token received. Long gap from `PLAN_START` = slow planner. |
| `PLAN_DONE` | Planning complete. `response_len=0` means the model returned nothing. |
| `PLAN_EXEC_START` | Execution phase begins. |
| `STEP_START` | A step is about to run. |
| `STEP_LLM_REQUEST` | PROMPT or GENCODE dispatched to the analysis LLM. |
| `STEP_FIRST_TOKEN` | First token of the PROMPT/GENCODE response received. |
| `STEP_LLM_DONE` | PROMPT/GENCODE response complete. |
| `STEP_DONE success=False` | A step failed — see `error=` field for reason. |
| `STEP_SKIPPED` | Step was skipped (dry-run or user denied). |

### Adjusting the stream timeout

The default timeout of 120 seconds applies per text token gap (not per full
response). Thinking models that are silent during reasoning will hit this limit
if they think for more than 2 minutes without emitting a token.

```bash
# Even tighter — abort after 30s of silence
aicli --stream-timeout 30 ...

# Patient mode for slow hardware (10-minute budget)
aicli --stream-timeout 600 ...

# No timeout at all (not recommended — hangs indefinitely on network issues)
aicli --stream-timeout 0 ...
```

---

## Examples

### Analyze a data file and write a report

```bash
echo "Read /tmp/data.csv, analyze for anomalies, write report to /tmp/report.md" | \
  aicli --model ollama/qwen3.5 --include-directories /tmp -y
```

### See the plan before executing (dry-run)

```bash
aicli --model ollama/qwen3.5 \
      --prompt-file prompts/analyze-sar.md \
      --include-directories ~/oracle-hc \
      --dry-run
```

### Generate a gnuplot chart from SAR data

```bash
aicli --model ollama/qwen3.5 \
      --prompt-file prompts/sar-memory.md \
      --include-directories ~/oracle-hc \
      --allow-exec -y
```

### Multi-model: plan with small model, analyze with large model

```bash
aicli --model ollama/qwen3.5 \
      --analysis-model ollama/batiai/qwen3.6-35b:q4 \
      --prompt-file prompts/full-hc-report.md \
      --include-directories ~/oracle-hc \
      --allow-exec -y
```

### Verbose mode — see substituted prompts

```bash
echo "Read /tmp/data.csv and write a summary to /tmp/summary.md" | \
  aicli --model ollama/qwen3.5 --include-directories /tmp --verbose -y
```

### Interactive REPL

```bash
aicli --model ollama/qwen3.5 --include-directories ~/project
# aicli> List the Python files in ~/project
# aicli> Read src/main.py and explain what it does
# aicli> Write a markdown summary of the project to ~/project/SUMMARY.md
# aicli> exit
```

### Custom planner system prompt

```bash
aicli --model ollama/qwen3.5 \
      --system-prompt-file prompts/custom-planner.md \
      --prompt-file prompts/task.md \
      --include-directories ~/project
```

### Trace a slow or hanging model

```bash
# Run with tracing and a 5-minute per-chunk timeout
echo "Analyze /tmp/data.csv and write a report to /tmp/report.md" | \
  aicli --model ollama/batiai/qwen3.6-35b:q3 \
        --include-directories /tmp \
        --trace /tmp/aicli.trace \
        --stream-timeout 300 \
        --auto-approve

# In another terminal — watch as events arrive in real time
tail -f /tmp/aicli.trace
```

### Abort immediately on any step failure

```bash
echo "Run the test suite and write a report" | \
  aicli --model ollama/qwen3.5 \
        --include-directories /tmp \
        --allow-exec \
        --on-error abort \
        -y
```

---

## Testing

### Run unit tests (no Ollama required)

```bash
python -m pytest tests/test_parser.py tests/test_executor.py \
                 tests/test_permissions.py tests/test_model_filter.py -v
```

### Integration tests (requires Ollama)

```bash
python -m pytest tests/test_drivers/test_ollama.py -v
```

### Test the V2 parser directly

```python
from aicli.core.plan_parser import parse_plan
from aicli.core.result_store import ResultStore

steps = parse_plan("READFILE: cat /tmp/data.csv\nPROMPT: Analyze this:\n{RESULT_OF_STEP_1}")
for s in steps:
    print(f"{s.number}. {s.keyword}: {s.arg}")
```

---

## Project Layout

```
aicli/
├── pyproject.toml
├── README.md
├── API.md                     # Internal API reference
├── ADD-LLM.md                 # How to add a new driver
├── src/
│   └── aicli/
│       ├── cli.py             # Entry point: arg parsing, run_task(), REPL
│       ├── config.py          # Config file loading and merging
│       ├── core/
│       │   ├── plan_parser.py     # Parses tagged step lists from LLM output
│       │   ├── result_store.py    # Step result storage + {RESULT_OF_STEP_N} substitution
│       │   ├── planner.py         # Sends task to LLM, returns plan text
│       │   ├── orchestrator.py    # Executes plan steps, dispatches PROMPT/GENCODE
│       │   ├── executor.py        # Low-level file I/O and shell execution
│       │   ├── actions.py         # ActionType / ActionRequest / ActionResult schema
│       │   ├── parser.py          # Legacy XML action block parser (kept for reference)
│       │   ├── session.py         # Conversation history (multi-turn)
│       │   ├── system_prompt.py   # Legacy system prompt builders
│       │   └── system_prompts/
│       │       └── default.md     # Built-in V2 planner system prompt
│       ├── drivers/
│       │   ├── base.py            # Abstract BaseDriver interface
│       │   ├── ollama.py          # Ollama REST API driver
│       │   ├── gemini.py          # Google Generative AI driver (Phase 3 stub)
│       │   ├── claude.py          # Anthropic API driver (Phase 3 stub)
│       │   ├── openai.py          # OpenAI API driver (Phase 3 stub)
│       │   └── registry.py        # Driver name → class lookup
│       └── output/
│           ├── renderer.py        # Streaming output + rich Markdown + plan display
│           ├── logger.py          # Session file logging
│           └── tracer.py          # Timing trace writer (--trace; line-buffered, survives kill)
└── tests/
    ├── test_parser.py
    ├── test_executor.py
    ├── test_permissions.py
    ├── test_retry.py
    ├── test_model_filter.py
    ├── test_cli_utils.py
    └── test_drivers/
        └── test_ollama.py
```
