"""Action block parser for system-prompt mode (universal fallback).

Parses <aicli_action type="...">...</aicli_action> blocks from model output.
"""

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


def split_text_and_actions(text: str) -> tuple[str, list[ActionRequest]]:
    """Return (clean_text, actions) where clean_text has action blocks removed."""
    actions = list(parse_action_blocks(text))
    clean = _ACTION_RE.sub("", text).strip()
    return clean, actions
