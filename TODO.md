# AICLI V2 — Project TODO

Status key: ✅ done · 🔄 in progress · ⬜ pending · ❌ blocked

---

## Phase 1 — Planning and File I/O (Ollama only)
All complete.

| # | Item | Status |
|---|------|--------|
| 1.1 | CLI skeleton: argparse, config file loading | ✅ |
| 1.2 | Ollama driver: send/receive via REST, streaming | ✅ |
| 1.3 | Built-in system prompt for task decomposition | ✅ |
| 1.4 | Plan parser: READFILE/WRITEFILE/LISTDIR/EXEC/PROMPT/GENCODE | ✅ |
| 1.5 | Result store: key-value, `{RESULT_OF_STEP_N}` substitution | ✅ |
| 1.6 | READFILE executor | ✅ |
| 1.7 | WRITEFILE executor + `--include-directories` path validation | ✅ |
| 1.8 | LISTDIR executor | ✅ |
| 1.9 | PROMPT executor + auto result injection | ✅ |
| 1.10 | GENCODE executor: LLM dispatch, fence stripping, SAVEAS write | ✅ |
| 1.11 | Confirmation prompts + `-y` flag | ✅ |
| 1.12 | Pipe mode, file mode, interactive REPL | ✅ |

---

## Phase 2 — Execution, Robustness, and UX
Nearly complete.

| # | Item | Status | Notes |
|---|------|--------|-------|
| 2.1 | EXEC executor + `--allow-exec` enforcement + timeout | ✅ | |
| 2.2 | `--dry-run` flag | ✅ | |
| 2.3 | `--verbose` flag | ✅ | |
| 2.4 | `--on-error` flag (continue/abort/ask) | ✅ | |
| 2.5 | Step progress display (step N of M) | ✅ | |
| 2.6 | Early warning when EXEC steps present but `--allow-exec` not set | ✅ | |
| 2.7 | Non-zero EXEC exit code shows meaningful error message | ✅ | |
| 2.8 | Session logging to file (`--log-sessions`) | ⬜ | SessionLogger exists; needs end-to-end test |
| 2.9 | Tests: plan_parser, result_store, orchestrator | ✅ | 131 passing |

---

## Phase 2b — Parser Robustness (ongoing)
Driven by real model test runs.

| # | Item | Status | Notes |
|---|------|--------|-------|
| P.1 | Bare path → `cat path` normalization (READFILE) | ✅ | |
| P.2 | `file=`, `path=`, `dir=` key-value normalization | ✅ | |
| P.3 | Outer code fence stripping | ✅ | |
| P.4 | Backtick-wrapped step lines | ✅ | |
| P.5 | `Step N:` prefix and `1.`/`1)` bullet tolerance | ✅ | |
| P.6 | `- ` / `* ` markdown list marker tolerance | ✅ | |
| P.7 | GENCODE: path-as-arg detection (`GENCODE: /path`) | ✅ | |
| P.8 | GENCODE: `→ /path` / `-> /path` embedded path extraction | ✅ | |
| P.9 | `[KEYWORD]` bracket format (glm-4.7-flash) | ✅ | |
| P.10 | Bare `KEYWORD` with no separator | ✅ | |
| P.11 | Thinking-mode timeout (batiai/qwen3.6-35b:q3 takes 18 min) | ⬜ | Add stream timeout or `/no_think` flag |
| P.12 | Re-run model compatibility test after prompt + parser changes | ⬜ | Run test-models script; update notes |

---

## Phase 3 — Multi-Driver and Multi-Model

| # | Item | Status | Notes |
|---|------|--------|-------|
| 3.1 | Driver abstract base class (formalized) | ✅ | base.py done |
| 3.2 | Gemini driver | ⬜ | Stub exists; needs real implementation |
| 3.3 | Claude (Anthropic) driver | ⬜ | Stub exists; needs real implementation |
| 3.4 | OpenAI driver | ⬜ | Stub exists; needs real implementation |
| 3.5 | `--list-models` for all drivers | ⬜ | Ollama done; others need it |
| 3.6 | `--analysis-model` flag (multi-model pipelines) | ✅ | |
| 3.7 | Cross-vendor multi-model support (plan Ollama, analyze Claude) | ⬜ | Depends on 3.2–3.4 |
| 3.8 | Integration tests for each driver | ⬜ | Depends on 3.2–3.4 |

---

## Phase 4 — Polish and Distribution

| # | Item | Status | Notes |
|---|------|--------|-------|
| 4.1 | Markdown rendering in terminal (rich) | ✅ | Renderer done |
| 4.2 | Per-project config files (`.aicli.yaml`) | ⬜ | |
| 4.3 | Customizable system prompts per task type | ⬜ | `--system-prompt-file` exists; needs per-task-type routing |
| 4.4 | pip/pipx packaging with entry point | ⬜ | pyproject.toml exists; needs verification |
| 4.5 | README and API docs | ✅ | Updated for V2 |
| 4.6 | Full test suite coverage ≥ 80% | ⬜ | Drivers and config not yet tested |

---

## Known Model Behaviour Issues

| Model | Issue | Workaround |
|-------|-------|-----------|
| batiai/qwen3.6-35b:q3 | Thinking mode: takes 18+ min, aborts | Add stream timeout / `--no-think` |
| batiai/qwen3.6-35b:q4 | Bare keyword format (no `:`) | ✅ Fixed in parser |
| glm-4.7-flash | `[KEYWORD]` bracket format | ✅ Fixed in parser |
| glm-4.7-flash:q4_K_M | GENCODE for prose (no SAVEAS) | System prompt improved |
| qwen3.5 | GENCODE: `→ /path` pattern | ✅ Fixed in parser |
| qwen3-coder:30b | Hallucinate files that don't exist | System prompt rule added |
| qwen2.5-coder:14b | GENCODE with `"""` as arg | System prompt improved |
| llama3.2 | No plan found (prose response) | Needs further tuning |

---

## Next Up (recommended order)

1. ⬜ P.11 — Handle thinking-mode timeout
2. ⬜ P.12 — Re-run model compatibility test
3. ⬜ 2.8  — Validate session logging end-to-end
4. ⬜ 3.2  — Gemini driver
5. ⬜ 3.3  — Claude driver
6. ⬜ 3.4  — OpenAI driver
