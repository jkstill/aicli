"""Action block parser for system-prompt mode (universal fallback).

Parses <aicli_action type="...">...</aicli_action> blocks from model output,
and also handles the <function=name><parameter=key>value</parameter></function>
format emitted by some models (e.g. qwen3-coder) when native tool calling
silently falls back to text output.
"""

import json
import re
from typing import Generator

from .actions import ActionRequest, ActionType, WriteMode

# Matches the outer tag and captures type + body.
_ACTION_RE = re.compile(
    r"<aicli_action\s+type=['\"](\w+)['\"]\s*>(.*?)</aicli_action>",
    re.DOTALL | re.IGNORECASE,
)

# Matches CONTENT heredoc inside write_file blocks.
_HEREDOC_RE = re.compile(r"<<<CONTENT\n(.*?)\nCONTENT>>>", re.DOTALL)

# JSON code block: {"name": "write_file", "arguments": {...}} (llama3.2, qwen2.5, etc.)
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(\{.*?\})\s*\n```", re.DOTALL)

# <function=name>...</function> format (some Qwen-family models).
_FUNC_BLOCK_RE = re.compile(
    r"<function=(\w+)\s*>(.*?)</function>",
    re.DOTALL | re.IGNORECASE,
)
_PARAM_BLOCK_RE = re.compile(
    r"<parameter=(\w+)\s*>(.*?)</parameter>",
    re.DOTALL | re.IGNORECASE,
)

_FUNC_CALL_ALIASES: dict[str, str] = {
    "filepath": "path",
    "file_path": "path",
    "filename": "path",
    "file_content": "content",
    "file_contents": "content",
    "text": "content",
    "file_mode": "mode",
    "directory": "path",
    "dir": "path",
    "dir_path": "path",
    "cmd": "command",
    "shell_command": "command",
    "cwd": "working_dir",
    "working_directory": "working_dir",
}

_FUNC_NAME_MAP: dict[str, ActionType] = {
    "write_file": ActionType.WRITE_FILE,
    "writefile": ActionType.WRITE_FILE,
    "read_file": ActionType.READ_FILE,
    "readfile": ActionType.READ_FILE,
    "list_directory": ActionType.LIST_DIRECTORY,
    "listdirectory": ActionType.LIST_DIRECTORY,
    "listdir": ActionType.LIST_DIRECTORY,
    "execute": ActionType.EXECUTE,
    "run": ActionType.EXECUTE,
    "search_files": ActionType.SEARCH_FILES,
    "searchfiles": ActionType.SEARCH_FILES,
}


def _parse_kv(body: str) -> dict[str, str]:
    """Parse simple key: value lines from action body, respecting heredoc for content."""
    result: dict[str, str] = {}

    # Extract heredoc content first so it isn't mangled by line parsing.
    heredoc_match = _HEREDOC_RE.search(body)
    if heredoc_match:
        result["content"] = heredoc_match.group(1)
        body = body[: heredoc_match.start()] + body[heredoc_match.end() :]

    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key and key not in result:
                result[key] = value

    return result


def _coerce_bool(value: str) -> bool:
    return value.lower() in ("true", "yes", "1")


def parse_action_blocks(text: str) -> Generator[ActionRequest, None, None]:
    """Yield ActionRequest objects for every valid action block found in text."""
    for match in _ACTION_RE.finditer(text):
        raw_type = match.group(1).lower()
        body = match.group(2)

        try:
            action_type = ActionType(raw_type)
        except ValueError:
            continue

        kv = _parse_kv(body)

        params: dict = {}
        match action_type:
            case ActionType.READ_FILE:
                if "path" not in kv:
                    continue
                params = {"path": kv["path"]}

            case ActionType.WRITE_FILE:
                if "path" not in kv or "content" not in kv:
                    continue
                raw_mode = kv.get("mode", "overwrite")
                try:
                    mode = WriteMode(raw_mode)
                except ValueError:
                    mode = WriteMode.OVERWRITE
                params = {"path": kv["path"], "content": kv["content"], "mode": mode}

            case ActionType.LIST_DIRECTORY:
                if "path" not in kv:
                    continue
                params = {
                    "path": kv["path"],
                    "recursive": _coerce_bool(kv.get("recursive", "false")),
                }

            case ActionType.EXECUTE:
                if "command" not in kv:
                    continue
                timeout_raw = kv.get("timeout", "30")
                try:
                    timeout = int(timeout_raw)
                except ValueError:
                    timeout = 30
                params = {
                    "command": kv["command"],
                    "working_dir": kv.get("working_dir") or None,
                    "timeout": timeout,
                }

            case ActionType.SEARCH_FILES:
                if "pattern" not in kv or "path" not in kv:
                    continue
                params = {
                    "pattern": kv["pattern"],
                    "path": kv["path"],
                    "type": kv.get("type", "glob"),
                }

        yield ActionRequest(action_type=action_type, params=params)


def _action_request_from_raw(func_name: str, raw: dict[str, str]) -> "ActionRequest | None":
    """Build an ActionRequest from a normalised param dict, or None if required params are missing."""
    action_type = _FUNC_NAME_MAP.get(func_name)
    if action_type is None:
        return None

    params: dict = {}
    match action_type:
        case ActionType.WRITE_FILE:
            if "path" not in raw or "content" not in raw:
                return None
            raw_mode = raw.get("mode", "overwrite")
            try:
                mode = WriteMode(raw_mode)
            except ValueError:
                mode = WriteMode.OVERWRITE
            params = {"path": raw["path"], "content": raw["content"], "mode": mode}

        case ActionType.READ_FILE:
            if "path" not in raw:
                return None
            params = {"path": raw["path"]}

        case ActionType.LIST_DIRECTORY:
            if "path" not in raw:
                return None
            params = {"path": raw["path"], "recursive": _coerce_bool(raw.get("recursive", "false"))}

        case ActionType.EXECUTE:
            if "command" not in raw:
                return None
            timeout_raw = raw.get("timeout", "30")
            try:
                timeout = int(timeout_raw)
            except ValueError:
                timeout = 30
            params = {
                "command": raw["command"],
                "working_dir": raw.get("working_dir") or None,
                "timeout": timeout,
            }

        case ActionType.SEARCH_FILES:
            if "pattern" not in raw or "path" not in raw:
                return None
            params = {"pattern": raw["pattern"], "path": raw["path"], "type": raw.get("type", "glob")}

        case _:
            return None

    return ActionRequest(action_type=action_type, params=params)


def parse_function_call_blocks(text: str) -> Generator[ActionRequest, None, None]:
    """Yield ActionRequests from <function=name><parameter=key>val</parameter></function> blocks."""
    for match in _FUNC_BLOCK_RE.finditer(text):
        func_name = match.group(1).lower()
        body = match.group(2)

        raw: dict[str, str] = {}
        for pm in _PARAM_BLOCK_RE.finditer(body):
            key = _FUNC_CALL_ALIASES.get(pm.group(1).strip(), pm.group(1).strip())
            raw[key] = pm.group(2).strip()

        req = _action_request_from_raw(func_name, raw)
        if req is not None:
            yield req


def parse_json_tool_call_blocks(text: str) -> Generator[ActionRequest, None, None]:
    """Yield ActionRequests from JSON code blocks like {"name": "write_file", "arguments": {...}}."""
    for match in _JSON_BLOCK_RE.finditer(text):
        try:
            obj = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue

        func_name = str(obj.get("name", "")).lower()
        args = obj.get("arguments") or obj.get("parameters") or {}
        if not isinstance(args, dict):
            continue

        raw = {
            _FUNC_CALL_ALIASES.get(k, k): str(v) if not isinstance(v, str) else v
            for k, v in args.items()
        }

        req = _action_request_from_raw(func_name, raw)
        if req is not None:
            yield req


def split_text_and_actions(text: str) -> tuple[str, list[ActionRequest]]:
    """Return (clean_text, actions) where clean_text has all action blocks removed."""
    actions = list(parse_action_blocks(text))
    clean = _ACTION_RE.sub("", text).strip()

    # Also handle <function=...> blocks (text-mode tool calls from some models).
    func_actions = list(parse_function_call_blocks(clean))
    if func_actions:
        actions.extend(func_actions)
        clean = _FUNC_BLOCK_RE.sub("", clean)

    # Also handle JSON code blocks: {"name": "write_file", "arguments": {...}}.
    json_actions = list(parse_json_tool_call_blocks(clean))
    if json_actions:
        actions.extend(json_actions)
        clean = _JSON_BLOCK_RE.sub("", clean)

    # Always strip stray <tool_call>/</ tool_call> tags some models append.
    clean = re.sub(r"</?\s*tool_call\s*>", "", clean, flags=re.IGNORECASE).strip()

    return clean, actions
