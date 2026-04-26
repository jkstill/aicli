# AICLI — Universal Agentic CLI for Large Language Models

## Project Summary

AICLI is a command-line interface that provides a unified, vendor-agnostic agentic layer for interacting with large language models. It follows the DBI/DBD architectural pattern from the Perl database ecosystem: a single user-facing tool (`aicli`) communicates through a standard internal API to vendor-specific driver modules that handle the translation to each LLM provider's native API.

The key problem AICLI solves: modern LLM CLIs like Gemini CLI provide agentic capabilities (file read/write, script execution, directory traversal) tightly coupled to a single vendor. Users who want to switch between providers — or use local models via Ollama — lose these capabilities entirely. AICLI decouples the agentic execution layer from the model layer, so switching from Gemini to Ollama to Claude to OpenAI is a flag change, not a rewrite.

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    aicli (CLI)                      │
│  - Prompt input (interactive + pipe/file modes)     │
│  - Streaming output with Markdown rendering         │
│  - Permission management (--include-directories)    │
│  - Session/conversation history                     │
│  - Action execution engine (file I/O, shell exec)   │
└──────────────────────┬──────────────────────────────┘
                       │ AICLI Internal API
                       │ (standardized action protocol)
┌──────────────────────┴──────────────────────────────┐
│                  AICLI Core API                     │
│  - Action schema (read_file, write_file, exec, ...) │
│  - Response normalization                           │
│  - Prompt augmentation (system prompt injection)    │
│  - Streaming protocol adapter                       │
└──────┬──────────┬──────────┬──────────┬─────────────┘
       │          │          │          │
  ┌────┴───┐ ┌───┴────┐ ┌───┴───┐ ┌───┴──────┐
  │ ollama │ │ gemini │ │claude │ │ openai   │
  │ driver │ │ driver │ │driver │ │ driver   │
  └────┬───┘ └───┬────┘ └───┬───┘ └───┬──────┘
       │         │          │          │
   Ollama    Gemini API  Anthropic  OpenAI
   REST API               API       API
```

## The Three Layers in Detail

### Layer 1: aicli (The CLI Frontend)

This is what the user interacts with. It is responsible for:

#### Prompt Handling
- Interactive mode (REPL with readline/history support)
- Pipe mode: `cat prompt.txt | aicli --model ollama/qwen3.5`
- File mode: `aicli --prompt-file prompt.txt --model ollama/qwen3.5`
- Multi-turn conversation with history
- System prompt injection from file or CLI flag

#### Permission and Sandbox Management
- `--include-directories <dir>[,<dir>,...]` — whitelist directories the model may read/write
- `--allow-exec` — permit the model to request shell command execution
- `--auto-approve` / `-y` — skip confirmation prompts (equivalent to gemini --yolo)
- Default: read-only access, no execution, confirmation required for writes

#### Action Execution Engine
This is the critical piece that makes AICLI agentic. When the model's response contains action requests, aicli:
1. Parses the response for structured action blocks
2. Validates the action against the current permission set
3. Prompts the user for confirmation (unless `-y`)
4. Executes the action
5. Feeds the result back to the model as context for the next turn

#### Output
- Stream model responses to the terminal
- Render Markdown by default (configurable)
- Log full sessions to file (optional)

### Layer 2: AICLI Core API (The Standard Protocol)

This layer defines the contract between the CLI frontend and the vendor drivers. It has two main components:

#### The Action Schema

A fixed vocabulary of actions the model can request. Each action is a structured object:

```yaml
actions:
  read_file:
    params: { path: string }
    returns: { content: string, error: string|null }

  write_file:
    params: { path: string, content: string, mode: "create"|"overwrite"|"append" }
    returns: { success: bool, error: string|null }

  list_directory:
    params: { path: string, recursive: bool }
    returns: { entries: list, error: string|null }

  execute:
    params: { command: string, working_dir: string|null, timeout: int }
    returns: { stdout: string, stderr: string, exit_code: int }

  search_files:
    params: { pattern: string, path: string, type: "glob"|"regex" }
    returns: { matches: list }
```

#### The System Prompt Contract

AICLI injects a system prompt that instructs the model how to request actions. This is the key to making non-agentic models behave agentically:

```
When you need to perform an action on the filesystem or execute a command,
emit an action block in the following format:

<aicli_action type="write_file">
path: /path/to/file.md
mode: create
content:
<<<CONTENT
(file content here)
CONTENT>>>
</aicli_action>

<aicli_action type="execute">
command: gnuplot script.gnuplot
working_dir: /home/user/charts
</aicli_action>

<aicli_action type="read_file">
path: /home/user/data/input.csv
</aicli_action>

You may emit multiple action blocks in a single response.
After each action is executed, you will receive the result and may continue.
```

#### Response Normalization

The Core API normalizes vendor-specific response formats into a standard stream:

```
Response = {
  text_chunks: [stream of text segments],
  action_requests: [list of parsed action blocks],
  metadata: { tokens_in, tokens_out, model, latency }
}
```

### Layer 3: Vendor Drivers

Each driver module implements a standard interface:

```
Driver Interface:
  configure(api_base, api_key, model, options) -> void
  send(messages[], system_prompt, stream=bool) -> ResponseStream
  list_models() -> string[]
  supports_native_tools() -> bool
  get_native_tool_schema() -> schema|null
```

#### Driver: ollama

- API: `http://<host>:11434/api/chat`
- Auth: None (local network)
- Streaming: Native SSE support
- Tool support: Newer Ollama versions support function calling for some models;
  driver should detect capability and use native tools when available,
  fall back to system-prompt-based action parsing when not
- Config: `OLLAMA_HOST` or `--api-base`

#### Driver: gemini

- API: Google Generative AI API
- Auth: API key via `GEMINI_API_KEY` or `--api-key`
- Streaming: Native
- Tool support: Native function calling — driver maps AICLI action schema
  to Gemini's tool declaration format
- Note: For users who already have Gemini CLI working, this driver provides
  an alternative path that keeps their prompts working across other backends

#### Driver: claude (Anthropic)

- API: `https://api.anthropic.com/v1/messages`
- Auth: API key via `ANTHROPIC_API_KEY`
- Streaming: Native SSE
- Tool support: Native tool use — driver maps AICLI action schema
  to Anthropic's tool_use format

#### Driver: openai

- API: `https://api.openai.com/v1/chat/completions`
- Auth: API key via `OPENAI_API_KEY`
- Streaming: Native SSE
- Tool support: Native function calling
- Note: Also covers OpenAI-compatible APIs (vLLM, etc.) via `--api-base`

## Implementation Language

**Python** is the recommended implementation language:

- All target APIs have mature Python SDKs or simple REST interfaces
- The LLM tooling ecosystem is Python-first
- Easy subprocess management for the execution engine
- Rich library support for streaming, YAML/JSON parsing, readline
- Users in this space (data engineers, DBAs, DevOps) generally have Python available
- Packaging via pip/pipx for easy installation

Alternatively, **Go** would produce a single static binary with no runtime dependencies,
which is attractive for distribution. The trade-off is less ecosystem support for
LLM client libraries.

## Key Design Decisions

### Action Parsing Strategy

This is the hardest problem in the system. Two approaches, not mutually exclusive:

**Approach A: System Prompt Parsing (Universal Fallback)**

Inject a system prompt that instructs the model to emit structured action blocks
(XML-like tags, fenced blocks with metadata, etc.). The AICLI parser scans model
output for these blocks and extracts them. This works with ANY model that can
follow formatting instructions, including small local models via Ollama.

Pros: Universal, no vendor API dependency
Cons: Fragile with weaker models, uses context window for instructions

**Approach B: Native Tool Calling (Preferred When Available)**

Use the vendor API's native function/tool calling mechanism. The driver translates
AICLI's action schema into the vendor's tool declaration format. The model emits
structured tool calls that the API returns as parsed objects — no output parsing needed.

Pros: Reliable, structured, no parsing ambiguity
Cons: Not all models/APIs support it (basic Ollama models don't)

**Recommended: Hybrid**

Each driver reports `supports_native_tools()`. When true, AICLI uses native tool
calling via the driver. When false, AICLI falls back to system-prompt-based
action parsing. This gives reliability with capable APIs and broad compatibility
with everything else.

### Permission Model

Permissions are restrictive by default and explicitly granted:

```bash
# Read-only, no execution (default)
aicli --model ollama/qwen3.5

# Allow writes within a project directory
aicli --model ollama/qwen3.5 --include-directories ~/project

# Allow writes and script execution
aicli --model ollama/qwen3.5 --include-directories ~/project --allow-exec

# Full auto-approve (yolo mode)
aicli --model ollama/qwen3.5 --include-directories ~/project --allow-exec -y
```

Path validation is enforced in the execution engine, NOT in the driver or the model.
The model can request any action; aicli decides whether to permit it. Symlink
traversal outside permitted directories is blocked.

### Configuration

```yaml
# ~/.config/aicli/config.yaml

defaults:
  model: ollama/qwen3.5
  output_format: markdown    # markdown | plain | json
  confirm_actions: true
  log_sessions: false
  log_dir: ~/.local/share/aicli/logs

drivers:
  ollama:
    api_base: http://192.168.1.53:11434
  gemini:
    api_key_env: GEMINI_API_KEY
  claude:
    api_key_env: ANTHROPIC_API_KEY
  openai:
    api_key_env: OPENAI_API_KEY

# Per-project overrides
# Place .aicli.yaml in project root
```

### Model Specification

Models are specified as `driver/model-name`:

```bash
aicli --model ollama/qwen3.5
aicli --model ollama/qwen3-coder:30b
aicli --model gemini/gemini-2.5-flash
aicli --model claude/claude-sonnet-4-20250514
aicli --model openai/gpt-4o
```

The part before the `/` selects the driver. The part after is passed to the driver
as the model identifier.

## CLI Usage Examples

```bash
# Interactive session with Ollama
aicli --model ollama/qwen3.5 --include-directories ~/oracle-hc

# Pipe a prompt file (non-interactive)
cat prompts/sar-analysis.md | aicli --model ollama/qwen3-coder:30b \
  --include-directories ~/oracle-hc -y

# Prompt file with auto-approve
aicli --model ollama/qwen3.5 \
  --prompt-file prompts/generate-gnuplot.md \
  --include-directories ~/oracle-hc/charts \
  --allow-exec -y

# Use Gemini API instead (same prompt, same permissions)
aicli --model gemini/gemini-2.5-flash \
  --prompt-file prompts/generate-gnuplot.md \
  --include-directories ~/oracle-hc/charts \
  --allow-exec -y

# System prompt override
aicli --model ollama/qwen3.5 \
  --system-prompt "Always output in Markdown. Never use JSON formatting." \
  --include-directories ~/oracle-hc

# List available models for a driver
aicli --model ollama --list-models
```

## Prototype Milestones

### Phase 1: Minimum Viable Agentic CLI (Ollama only)
- [ ] CLI skeleton: argument parsing, config file loading
- [ ] Ollama driver: send/receive via REST API, streaming output
- [ ] System prompt injection for action block format
- [ ] Action parser: extract action blocks from model output
- [ ] write_file action with --include-directories enforcement
- [ ] read_file action
- [ ] Confirmation prompts (and -y flag)
- [ ] Pipe mode: accept prompt from stdin

**Success criteria:** Can pipe an existing oracle-hc prompt to aicli, have the
Ollama model generate gnuplot scripts and markdown files, and have aicli write
them to the designated directory.

### Phase 2: Execution and Multi-Turn
- [ ] execute action with --allow-exec enforcement
- [ ] Action result feedback loop (execute → feed result → continue)
- [ ] Interactive REPL mode with conversation history
- [ ] list_directory and search_files actions
- [ ] Session logging

### Phase 3: Multi-Driver Support
- [ ] Driver interface formalization
- [ ] Gemini driver (API-based, not CLI wrapper)
- [ ] Claude driver
- [ ] OpenAI driver
- [ ] Native tool calling for drivers that support it
- [ ] Hybrid parsing (native tools preferred, system prompt fallback)

### Phase 4: Polish
- [ ] Markdown rendering in terminal (via rich or similar)
- [ ] Per-project config files (.aicli.yaml)
- [ ] Model listing per driver
- [ ] pip/pipx packaging
- [ ] Documentation

## Project Structure

```
aicli/
├── pyproject.toml
├── README.md
├── src/
│   └── aicli/
│       ├── __init__.py
│       ├── cli.py              # Argument parsing, main entry point
│       ├── config.py           # Config file loading, defaults
│       ├── core/
│       │   ├── __init__.py
│       │   ├── actions.py      # Action schema definitions
│       │   ├── parser.py       # Action block parser (system prompt mode)
│       │   ├── executor.py     # Action execution engine + permissions
│       │   └── session.py      # Conversation history, multi-turn state
│       ├── drivers/
│       │   ├── __init__.py
│       │   ├── base.py         # Abstract driver interface
│       │   ├── ollama.py       # Ollama REST API driver
│       │   ├── gemini.py       # Google Generative AI driver
│       │   ├── claude.py       # Anthropic API driver
│       │   └── openai.py       # OpenAI API driver
│       └── output/
│           ├── __init__.py
│           ├── renderer.py     # Markdown rendering, streaming display
│           └── logger.py       # Session logging
└── tests/
    ├── test_parser.py
    ├── test_executor.py
    ├── test_permissions.py
    └── test_drivers/
        └── test_ollama.py
```

## Prior Art and Differentiation

| Tool | Agentic? | Multi-vendor? | File I/O? | Exec? | Overhead |
|------|----------|---------------|-----------|-------|----------|
| Gemini CLI | Yes | No (Gemini only) | Yes | Yes | Low |
| Claude Code | Yes | No (Claude only) | Yes | Yes | Low |
| Open Interpreter | Yes | Yes (via LiteLLM) | Yes | Yes | High |
| Aider | Partial | Yes | Yes (code) | No | Medium |
| Goose | Yes | Partial | Yes | Yes | Medium |
| ollama run | No | No (Ollama only) | No | No | None |
| **AICLI** | **Yes** | **Yes** | **Yes** | **Yes** | **Low** |

AICLI's differentiator is the combination of low overhead, broad vendor support,
and a clean permission model — specifically designed for users who have existing
prompt-based workflows and want to switch models without rewriting their pipelines.

## Notes for Development with Claude Code

When implementing this project with Claude Code:

1. Start with Phase 1. Get file writing working with Ollama before anything else.
2. The action parser is the riskiest component — build it early and test it with
   real model output from qwen3.5 and qwen3-coder.
3. Use `httpx` for async HTTP to Ollama (not `requests`) — streaming support is
   better and it's what LiteLLM uses internally.
4. The system prompt for action blocks needs iteration. Start simple (XML-like tags),
   test with the target models, and refine based on what they actually produce.
5. Keep the executor completely separate from the drivers. The driver sends prompts
   and receives text. The executor parses text and performs actions. They never
   know about each other.
6. Test with the oracle-hc prompt set as the real-world validation — if it can
   generate gnuplot scripts, run them, and write markdown reports across different
   models, the architecture is working.
