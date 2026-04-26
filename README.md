# aicli — Universal Agentic CLI for Large Language Models

aicli is a command-line tool that gives any LLM agentic capabilities: reading and
writing files, listing directories, searching for files, and executing shell commands.
Switch between Ollama (local), Gemini, Claude, or OpenAI with a single flag. Your
prompts and workflows stay the same regardless of which model is running them.

```
Architecture (DBI/DBD pattern):

  User prompt
      │
      ▼
  aicli (CLI frontend)          ← permission enforcement, action loop, output
      │ AICLI Core API
      ▼
  Core layer                    ← action schema, parser, executor, session
      │
      ▼
  Vendor driver                 ← ollama | gemini | claude | openai
      │
      ▼
  LLM provider                  ← local Ollama / cloud API
```

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Input Modes](#input-modes)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Permission Model](#permission-model)
- [Action Types](#action-types)
- [Model Selection](#model-selection)
- [Model Compatibility](#model-compatibility)
- [Examples](#examples)
- [Testing](#testing)
- [Project Layout](#project-layout)

---

## Installation

**Requirements:** Python 3.11+

```bash
# From the project root — editable install (recommended for development)
pip install -e .

# Or install into a specific Python interpreter
python3.11 -m pip install -e .
```

Dependencies installed automatically: `httpx`, `pyyaml`, `rich`, `click`.

Optional driver extras (Phase 3, not yet implemented):

```bash
pip install -e ".[gemini]"     # adds google-generativeai
pip install -e ".[anthropic]"  # adds anthropic SDK
pip install -e ".[openai]"     # adds openai SDK
```

---

## Quick Start

```bash
# Simplest possible run — interactive REPL with your configured default model
aicli

# One-shot question via pipe
echo "Explain what a gnuplot script is in two sentences." | aicli

# Write a file (requires --include-directories to allow writes)
echo "Write a hello-world bash script to /tmp/hello.sh" | \
  aicli --model ollama/glm-4.7-flash:latest \
        --include-directories /tmp \
        --auto-approve

# Full agentic session: write a script, run it, report output
aicli --model ollama/glm-4.7-flash:latest \
      --include-directories ~/project \
      --allow-exec \
      --auto-approve \
      --prompt-file prompts/generate-report.md
```

---

## Input Modes

aicli accepts prompts in three ways, tried in this order:

### 1. Pipe mode

If stdin is not a terminal, the entire stdin is read as the prompt:

```bash
echo "Summarise /etc/os-release" | aicli --model ollama/glm-4.7-flash:latest -y

cat my-prompt.txt | aicli --model ollama/glm-4.7-flash:latest \
  --include-directories ~/project -y
```

Pipe mode is non-interactive and exits after one turn (including any action
rounds the model triggers).

### 2. File mode

Pass a prompt file with `--prompt-file` / `-f`. The file is read once and then
the process exits:

```bash
aicli --prompt-file prompts/analyze-sar.md \
      --model ollama/glm-4.7-flash:latest \
      --include-directories ~/reports \
      --allow-exec -y
```

### 3. Interactive REPL

If neither pipe nor file mode applies, aicli drops into an interactive session.
Each prompt you type is one turn; the conversation history is preserved across turns.

```
$ aicli --model ollama/glm-4.7-flash:latest --include-directories ~/project

aicli | driver=ollama model=glm-4.7-flash:latest native_tools=True ...
Interactive mode — type 'exit' or Ctrl-D to quit.

aicli> What files are in the current directory?
...
aicli> Now read the pyproject.toml and summarise it.
...
aicli> exit
```

---

## CLI Reference

```
Usage: aicli [OPTIONS]

  aicli — universal agentic CLI for large language models.

Options:
  -m, --model TEXT                Model as driver/model-name
                                  (e.g. ollama/glm-4.7-flash:latest)
  -f, --prompt-file PATH          Read prompt from file
  -s, --system-prompt TEXT        Inline system prompt
      --system-prompt-file PATH   System prompt read from file
  -d, --include-directories TEXT  Comma-separated directories the model may
                                  write to (e.g. ~/project,/tmp)
      --allow-exec                Permit shell command execution
  -y, --auto-approve              Skip all confirmation prompts (yolo mode)
      --no-markdown               Disable rich Markdown rendering
      --list-models               List available models for the driver and exit
      --api-base TEXT             Override the driver's API base URL
      --api-key TEXT              Override the driver's API key
      --log-sessions              Write full session log to file
  -h, --help                      Show this message and exit.
```

### Option details

| Option | Default | Description |
|--------|---------|-------------|
| `--model` | from config | `driver/model-name`. The part before `/` selects the driver. |
| `--include-directories` | none | Comma-separated absolute or relative paths. The model may only **write** inside these. Reads are unrestricted. |
| `--allow-exec` | off | Enable the `execute` action. Disabled by default. |
| `--auto-approve` / `-y` | off | Skip Y/n confirmation for every action. Equivalent to `--yolo`. |
| `--no-markdown` | off | Print raw text instead of rich-rendered Markdown. Useful in scripts. |
| `--log-sessions` | off | Append full turn logs to `~/.local/share/aicli/logs/session_<ts>.log`. |
| `--api-base` | from config | Overrides the driver's API base URL for this run only. |

---

## Configuration

aicli loads configuration from two files, merged in order (later wins):

1. `~/.config/aicli/config.yaml` — global user config
2. `.aicli.yaml` — per-project config (in current working directory)

### Full example config

```yaml
# ~/.config/aicli/config.yaml

defaults:
  model: ollama/glm-4.7-flash:latest
  output_format: markdown        # markdown | plain
  confirm_actions: true
  log_sessions: false
  log_dir: ~/.local/share/aicli/logs

drivers:
  ollama:
    api_base: http://lestrade:11434   # remote Ollama host
  gemini:
    api_key_env: GEMINI_API_KEY       # read key from environment variable
  claude:
    api_key_env: ANTHROPIC_API_KEY
  openai:
    api_key_env: OPENAI_API_KEY
```

### Per-project override

Place a `.aicli.yaml` at the root of your project:

```yaml
# .aicli.yaml  (project root)
defaults:
  model: ollama/qwen3-coder:30b

drivers:
  ollama:
    api_base: http://localhost:11434
```

Any key in `.aicli.yaml` overrides the global config. Missing keys fall back to
the global config, then to built-in defaults.

---

## Permission Model

Permissions are restrictive by default and must be explicitly granted.

```
Reads:    always allowed — any path the model requests can be read
Writes:   only paths inside --include-directories
Execute:  only if --allow-exec is set
Approval: confirmation prompt per action, unless -y
```

### How write permissions work

`--include-directories` takes a comma-separated list of directory paths. A write
action is permitted if and only if its resolved path is inside at least one listed
directory. Symlink traversal outside the allowed set is blocked.

```bash
# Only /tmp is writable
aicli --include-directories /tmp

# Multiple directories
aicli --include-directories ~/project,/tmp

# Subdirectories of an allowed dir are also allowed
aicli --include-directories ~/project
# → writes to ~/project/charts/output.png are OK
# → writes to /etc/passwd are denied
```

### Confirmation prompts

Unless `-y` is passed, every action prints a summary and asks `[Y/n]`:

```
Action: write_file — /tmp/report.md (mode=WriteMode.OVERWRITE)
  Allow write_file? [Y/n]
```

Press Enter or `y` to allow, `n` to skip that action.

---

## Action Types

The model can request any of five actions. Each action is validated and executed
by the aicli executor, not the driver.

### `read_file`

Read the contents of any file (no directory restriction).

```
Parameters:
  path  (required)  Absolute path to read.
```

### `write_file`

Write content to a file. The path must be inside an `--include-directories` dir.

```
Parameters:
  path     (required)  Absolute path to write.
  content  (required)  File content (any text).
  mode     (required)  one of: overwrite | append | create
                       "create" fails if the file already exists.
```

### `list_directory`

List entries in a directory.

```
Parameters:
  path       (required)  Directory to list.
  recursive  (optional)  true | false (default false)
```

### `execute`

Run a shell command. Requires `--allow-exec`.

```
Parameters:
  command      (required)  Shell command string (passed to /bin/sh).
  working_dir  (optional)  Working directory for the command.
  timeout      (optional)  Seconds before timeout (default 30).
```

### `search_files`

Search for files by glob or regex pattern.

```
Parameters:
  pattern  (required)  Search pattern.
  path     (required)  Root directory to search under.
  type     (optional)  glob | regex (default glob)
```

---

## Model Selection

Models are specified as `driver/model-name`:

```bash
aicli --model ollama/glm-4.7-flash:latest
aicli --model ollama/qwen3-coder:30b
aicli --model ollama/batiai/qwen3.6-35b:q3
aicli --model gemini/gemini-2.5-flash        # Phase 3
aicli --model claude/claude-sonnet-4-6       # Phase 3
aicli --model openai/gpt-4o                  # Phase 3
```

The part before the first `/` is the driver name. Everything after is passed
verbatim to the driver as the model identifier.

### Listing available models

```bash
# List all models available on the configured Ollama server
aicli --model ollama --list-models

# Or with a specific host
aicli --model ollama --list-models --api-base http://lestrade:11434
```

---

## Model Compatibility

aicli uses a hybrid tool strategy: it queries the Ollama `/api/show` endpoint
for each model's declared capabilities before the first request.

| Capability | Strategy | How actions are communicated |
|------------|----------|------------------------------|
| `tools` | Native tool calling | Model emits structured JSON tool calls |
| none | System prompt (fallback) | Model emits `<aicli_action>` XML blocks |

The driver automatically disables thinking mode (`think: false`) for models
that declare the `thinking` capability, because extended thinking re-activates
RLHF-trained refusals on file operations in the qwen3/glm4 families.

### Tested models on Ollama (host: lestrade:11434)

| Model | Tool support | Agentic file ops | Notes |
|-------|-------------|-----------------|-------|
| `glm-4.7-flash:latest` | Native (tools) | Excellent | **Recommended default.** Consistent, uses correct write_file params. |
| `glm-4.7-flash:q4_K_M` | Native (tools) | Excellent | Quantised variant; same behaviour. |
| `batiai/qwen3.6-35b:q3` | Native (tools) | Good | Uses `execute` (shell redirect) for file writes. Requires `--allow-exec`. |
| `qwen3-coder:30b` | Native (tools) | Poor | RLHF refusal overrides tool calls for file operations. |
| `qwen3.5:latest` / `qwen3.5:9b` | Native (tools) | Poor | Same refusal problem as qwen3-coder. |
| `qwen2.5-coder:14b` | Native (tools) | Poor | Refuses file ops despite tool availability. |
| `llama3:latest` | None | N/A | Text-only; system prompt fallback. No file ops. |
| `gemma3:12b` | None | N/A | Text-only; system prompt fallback. No file ops. |

**Summary:** For agentic file work, use `glm-4.7-flash:latest`. For large-context
code generation or analysis without file I/O, `qwen3-coder:30b` and
`batiai/qwen3.6-35b:q3` are strong choices with `--allow-exec`.

---

## Examples

### Generate and write a file

```bash
echo "Write a Python script to /tmp/hello.py that prints 'Hello, World!'" | \
  aicli --model ollama/glm-4.7-flash:latest \
        --include-directories /tmp \
        --auto-approve
```

### Multi-action pipeline: write, make executable, run

```bash
echo "Create /tmp/greet.sh that echoes 'aicli works!', make it executable, then run it." | \
  aicli --model ollama/glm-4.7-flash:latest \
        --include-directories /tmp \
        --allow-exec \
        --auto-approve
```

### Analyse an existing project

```bash
aicli --model ollama/glm-4.7-flash:latest \
      --prompt-file prompts/analyze.md
# reads are always allowed; no --include-directories needed for read-only work
```

### Pipe mode — non-interactive batch job

```bash
cat <<'EOF' | aicli --model ollama/glm-4.7-flash:latest \
                    --include-directories ~/reports \
                    --allow-exec -y
Read /var/log/syslog (last 100 lines), identify any ERROR entries,
and write a summary to ~/reports/syslog-errors.md.
EOF
```

### Interactive REPL with conversation history

```bash
aicli --model ollama/glm-4.7-flash:latest \
      --include-directories ~/project \
      --allow-exec
# aicli> What is in ~/project?
# aicli> Read the main Python file and explain it.
# aicli> Add a docstring to the main() function and save it.
# aicli> exit
```

### Point at a remote Ollama host for one run

```bash
echo "What is 2+2?" | \
  aicli --model ollama/qwen3.5:latest \
        --api-base http://192.168.1.53:11434
```

### Custom system prompt

```bash
aicli --model ollama/glm-4.7-flash:latest \
      --system-prompt "Always respond in British English. Never use American spellings." \
      --include-directories ~/project
```

### Combine system prompt file with user prompt file

```bash
aicli --model ollama/glm-4.7-flash:latest \
      --system-prompt-file prompts/oracle-hc-context.md \
      --prompt-file prompts/generate-gnuplot.md \
      --include-directories ~/oracle-hc/charts \
      --allow-exec -y
```

### Session logging

```bash
aicli --model ollama/glm-4.7-flash:latest \
      --include-directories ~/project \
      --log-sessions
# Logs written to ~/.local/share/aicli/logs/session_<timestamp>.log
```

---

## Testing

### Run all tests

```bash
python3.11 -m pytest tests/ -v
```

### Unit tests only (no live Ollama required)

```bash
python3.11 -m pytest tests/test_parser.py tests/test_executor.py tests/test_permissions.py -v
```

### Integration tests (requires Ollama at configured host)

```bash
python3.11 -m pytest tests/test_drivers/test_ollama.py -v
```

The integration tests use `qwen3.5:latest` and only test basic streaming/listing,
not full agentic round-trips. They run in about 10 seconds.

### Test categories

| File | Tests | Requires Ollama |
|------|-------|----------------|
| `tests/test_parser.py` | 9 — action block XML parsing | No |
| `tests/test_executor.py` | 14 — file read/write/exec/search + permissions | No |
| `tests/test_permissions.py` | 4 — symlink traversal, path traversal, multi-dir | No |
| `tests/test_drivers/test_ollama.py` | 4 — model list, streaming, system prompt | Yes |

---

## Project Layout

```
aicli/
├── pyproject.toml
├── README.md
├── API.md
├── ADD-LLM.md
├── src/
│   └── aicli/
│       ├── cli.py              # Entry point: argument parsing, action loop, REPL
│       ├── config.py           # Config file loading and merging
│       ├── core/
│       │   ├── actions.py      # Action schema (ActionType, ActionRequest, ActionResult)
│       │   ├── parser.py       # XML action block parser (system-prompt fallback)
│       │   ├── executor.py     # Action execution engine + permission enforcement
│       │   ├── session.py      # Conversation history (multi-turn state)
│       │   └── system_prompt.py # System prompt builders for each tool strategy
│       ├── drivers/
│       │   ├── base.py         # Abstract BaseDriver interface
│       │   ├── ollama.py       # Ollama REST API driver (Phase 1)
│       │   ├── gemini.py       # Google Generative AI driver (Phase 3 stub)
│       │   ├── claude.py       # Anthropic API driver (Phase 3 stub)
│       │   ├── openai.py       # OpenAI API driver (Phase 3 stub)
│       │   └── registry.py     # Driver name → class lookup
│       └── output/
│           ├── renderer.py     # Streaming output + rich Markdown rendering
│           └── logger.py       # Session file logging
└── tests/
    ├── test_parser.py
    ├── test_executor.py
    ├── test_permissions.py
    └── test_drivers/
        └── test_ollama.py
```
