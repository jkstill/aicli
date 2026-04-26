"""aicli — main entry point. Argument parsing, prompt loop, action dispatch."""

import signal
import sys
from pathlib import Path

import click

from .config import driver_config, load_config, resolve_api_key
from .core.actions import ActionRequest, ActionType, WriteMode
from .core.executor import Executor
from .core.executor import PermissionError as ExecPermissionError
from .core.parser import split_text_and_actions
from .core.session import Session
from .core.system_prompt import (
    TOOL_RETRY_NUDGE,
    build_native_tools_system_prompt,
    build_system_prompt,
)
from .drivers.base import NativeToolCall, ResponseChunk
from .drivers.registry import get_driver
from .output.logger import SessionLogger
from .output.renderer import Renderer


def _parse_model(model_str: str) -> tuple[str, str]:
    """Split 'driver/model-name' into (driver, model). Defaults to ollama."""
    if "/" in model_str:
        driver, _, model = model_str.partition("/")
        return driver.lower(), model
    return "ollama", model_str


def _action_summary(req: ActionRequest) -> str:
    match req.action_type:
        case ActionType.READ_FILE:
            return req.get("path", "")
        case ActionType.WRITE_FILE:
            return f"{req.get('path', '')} (mode={req.get('mode', '')})"
        case ActionType.LIST_DIRECTORY:
            return req.get("path", "")
        case ActionType.EXECUTE:
            return req.get("command", "")
        case ActionType.SEARCH_FILES:
            return f"{req.get('pattern', '')} in {req.get('path', '')}"
        case _:
            return ""


_PARAM_ALIASES: dict[str, str] = {
    # write_file / read_file
    "file_path": "path",
    "filepath": "path",
    "filename": "path",
    "file_content": "content",
    "file_contents": "content",
    "text": "content",
    "file_mode": "mode",
    # list_directory
    "directory": "path",
    "dir": "path",
    "dir_path": "path",
    # execute
    "cmd": "command",
    "shell_command": "command",
    "cwd": "working_dir",
    "working_directory": "working_dir",
}


def _normalize_params(params: dict) -> dict:
    """Remap model-specific parameter aliases to canonical names."""
    return {_PARAM_ALIASES.get(k, k): v for k, v in params.items()}


def _infer_action_type(name: str, params: dict) -> ActionType | None:
    """Infer action type from function name (with fuzzy matching) or argument keys."""
    name_lower = name.lower().replace("_", "").replace("-", "").replace(".", "")

    # Exact/canonical names first.
    _NAME_MAP = {
        "readfile": ActionType.READ_FILE,
        "writefile": ActionType.WRITE_FILE,
        "listdirectory": ActionType.LIST_DIRECTORY,
        "listdir": ActionType.LIST_DIRECTORY,
        "execute": ActionType.EXECUTE,
        "run": ActionType.EXECUTE,
        "searchfiles": ActionType.SEARCH_FILES,
        "search": ActionType.SEARCH_FILES,
        "find": ActionType.SEARCH_FILES,
        "write": ActionType.WRITE_FILE,
        "read": ActionType.READ_FILE,
        "save": ActionType.WRITE_FILE,
    }
    if name_lower in _NAME_MAP:
        return _NAME_MAP[name_lower]

    # Substring matching.
    for fragment, atype in [
        ("write", ActionType.WRITE_FILE),
        ("save", ActionType.WRITE_FILE),
        ("create", ActionType.WRITE_FILE),
        ("read", ActionType.READ_FILE),
        ("get", ActionType.READ_FILE),
        ("list", ActionType.LIST_DIRECTORY),
        ("dir", ActionType.LIST_DIRECTORY),
        ("exec", ActionType.EXECUTE),
        ("run", ActionType.EXECUTE),
        ("command", ActionType.EXECUTE),
        ("search", ActionType.SEARCH_FILES),
        ("find", ActionType.SEARCH_FILES),
    ]:
        if fragment in name_lower:
            return atype

    # Fall back to argument-key heuristics.
    keys = set(params.keys())
    if "command" in keys:
        return ActionType.EXECUTE
    if "pattern" in keys:
        return ActionType.SEARCH_FILES
    if "recursive" in keys:
        return ActionType.LIST_DIRECTORY
    if "content" in keys or "file_content" in keys or "text" in keys:
        return ActionType.WRITE_FILE
    if "path" in keys or "file_path" in keys or "filepath" in keys:
        return ActionType.READ_FILE

    return None


def _native_call_to_action_request(tc: NativeToolCall) -> ActionRequest | None:
    params = _normalize_params(tc.params)

    try:
        action_type = ActionType(tc.name)
    except ValueError:
        action_type = _infer_action_type(tc.name, params)
        if action_type is None:
            return None

    if action_type == ActionType.WRITE_FILE:
        params.setdefault("mode", WriteMode.OVERWRITE)
        if "mode" in params and not isinstance(params["mode"], WriteMode):
            try:
                params["mode"] = WriteMode(params["mode"])
            except ValueError:
                params["mode"] = WriteMode.OVERWRITE

    return ActionRequest(action_type=action_type, params=params)


def _stream_response(driver, messages: list[dict], system_prompt: str, renderer: Renderer):
    """Stream a response from the driver, writing chunks to renderer.
    Returns (full_text, done_chunk) where done_chunk carries metadata/tool_calls.
    """
    done_chunk: ResponseChunk | None = None
    for chunk in driver.send(messages, system_prompt=system_prompt, stream=True):
        if chunk.done:
            done_chunk = chunk
            break
        if chunk.text:
            renderer.stream_chunk(chunk.text)
    return renderer._buffer, done_chunk


def run_turn(
    prompt: str,
    session: Session,
    driver,
    executor: Executor,
    renderer: Renderer,
    logger: SessionLogger,
    auto_approve: bool,
    use_native_tools: bool,
    system_prompt: str,
    max_action_rounds: int = 10,
    tool_retries: int = 2,
) -> None:
    """Execute one user prompt, potentially looping through multiple action rounds."""
    session.add_user(prompt)
    logger.log("user", prompt)

    tool_retry_count = 0

    for _round in range(max_action_rounds):
        messages = session.as_ollama_messages()
        renderer._buffer = ""

        full_text, done_chunk = _stream_response(driver, messages, system_prompt, renderer)
        renderer.finalize()

        logger.log("assistant", full_text)

        # Parse actions from the response.
        if use_native_tools and done_chunk and done_chunk.native_tool_calls:
            actions = [
                req for tc in done_chunk.native_tool_calls
                if (req := _native_call_to_action_request(tc)) is not None
            ]
        else:
            _, actions = split_text_and_actions(full_text)

        # Native-tools retry: model responded with text but no tool calls.
        # Inject a nudge and loop without advancing the visible turn.
        if use_native_tools and not actions and full_text.strip() and tool_retry_count < tool_retries:
            tool_retry_count += 1
            renderer.print_info(f"No tool call received — retrying ({tool_retry_count}/{tool_retries})")
            session.add_assistant(full_text)
            session.add_tool_result(TOOL_RETRY_NUDGE)
            logger.log("tool", f"[retry nudge {tool_retry_count}]")
            continue

        session.add_assistant(full_text)

        if not actions:
            break

        # Execute each requested action.
        result_parts: list[str] = []
        for req in actions:
            summary = _action_summary(req)
            renderer.print_action_header(req.action_type.value, summary)

            if not auto_approve:
                if not renderer.confirm(f"  Allow {req.action_type.value}?"):
                    renderer.print_action_result(False, "Skipped by user.")
                    result_parts.append(f"[Action {req.action_type.value} skipped by user]")
                    continue

            try:
                result = executor.execute(req)
            except ExecPermissionError as e:
                renderer.print_action_result(False, str(e))
                result_parts.append(f"[Permission denied: {e}]")
                continue

            msg = result.error or "Done."
            renderer.print_action_result(result.success, msg)
            ctx = result.to_context_string()
            result_parts.append(ctx)
            logger.log("tool", ctx)

        # Feed results back to the model.
        combined = "\n\n".join(result_parts)
        session.add_tool_result(combined)

    else:
        renderer.print_warning("Maximum action rounds reached.")


@click.command()
@click.option("--model", "-m", default=None, help="Model as driver/model-name (e.g. ollama/qwen3.5)")
@click.option("--prompt-file", "-f", "prompt_file", type=click.Path(exists=True), default=None,
              help="Read prompt from file")
@click.option("--system-prompt", "-s", "system_prompt", default=None, help="System prompt text")
@click.option("--system-prompt-file", "system_prompt_file", type=click.Path(exists=True), default=None,
              help="System prompt from file")
@click.option("--include-directories", "-d", "include_directories", default=None,
              help="Comma-separated writable directories")
@click.option("--allow-exec", "allow_exec", is_flag=True, default=False,
              help="Permit shell command execution")
@click.option("--auto-approve", "-y", "auto_approve", is_flag=True, default=False,
              help="Skip confirmation prompts")
@click.option("--no-markdown", "no_markdown", is_flag=True, default=False,
              help="Disable Markdown rendering")
@click.option("--list-models", "list_models", is_flag=True, default=False,
              help="List available models for the driver and exit")
@click.option("--api-base", "api_base", default=None, help="Override driver API base URL")
@click.option("--api-key", "api_key", default=None, help="Override driver API key")
@click.option("--log-sessions", "log_sessions", is_flag=True, default=False,
              help="Log session to file")
def main(
    model,
    prompt_file,
    system_prompt,
    system_prompt_file,
    include_directories,
    allow_exec,
    auto_approve,
    no_markdown,
    list_models,
    api_base,
    api_key,
    log_sessions,
):
    """aicli — universal agentic CLI for large language models."""

    config = load_config()
    model = model or config.get("model", "ollama/qwen3.5")
    driver_name, model_name = _parse_model(model)

    driver = get_driver(driver_name)
    dcfg = driver_config(config, driver_name)
    effective_base = api_base or dcfg.get("api_base", "")
    effective_key = api_key or resolve_api_key(dcfg)
    driver.configure(effective_base, effective_key, model_name)

    if list_models:
        for name in driver.list_models():
            click.echo(name)
        return

    allowed_dirs = [d.strip() for d in include_directories.split(",")] if include_directories else []
    executor = Executor(allowed_dirs=allowed_dirs, allow_exec=allow_exec)

    user_sp = ""
    if system_prompt_file:
        user_sp = Path(system_prompt_file).read_text()
    elif system_prompt:
        user_sp = system_prompt

    use_native = driver.supports_native_tools()
    if use_native:
        effective_system = build_native_tools_system_prompt(user_sp)
    else:
        effective_system = build_system_prompt(user_sp)

    use_markdown = not no_markdown and config.get("output_format", "markdown") == "markdown"
    renderer = Renderer(markdown=use_markdown)
    log_dir = config.get("log_dir", "~/.local/share/aicli/logs")
    logger = SessionLogger(
        log_dir=log_dir,
        enabled=log_sessions or config.get("log_sessions", False),
    )

    session = Session(system_prompt=effective_system)

    renderer.print_info(
        f"aicli | driver={driver_name} model={model_name} "
        f"native_tools={use_native} exec={allow_exec} "
        f"dirs={allowed_dirs if allowed_dirs else 'none'}"
    )

    # Pipe mode: stdin not a tty, no prompt file.
    if not sys.stdin.isatty() and not prompt_file:
        prompt_text = sys.stdin.read().strip()
        if prompt_text:
            run_turn(prompt_text, session, driver, executor, renderer, logger,
                     auto_approve, use_native, effective_system)
        logger.close()
        return

    # File mode.
    if prompt_file:
        prompt_text = Path(prompt_file).read_text().strip()
        run_turn(prompt_text, session, driver, executor, renderer, logger,
                 auto_approve, use_native, effective_system)
        logger.close()
        return

    # Interactive REPL.
    renderer.print_info("Interactive mode — type 'exit' or Ctrl-D to quit.")

    def _sigint(sig, frame):
        print()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)

    while True:
        try:
            prompt_text = input("\naicli> ").strip()
        except EOFError:
            print()
            break

        if not prompt_text:
            continue
        if prompt_text.lower() in ("exit", "quit", "q"):
            break

        run_turn(prompt_text, session, driver, executor, renderer, logger,
                 auto_approve, use_native, effective_system)

    logger.close()


if __name__ == "__main__":
    main()
