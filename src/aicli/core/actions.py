"""Action schema definitions — the standard vocabulary of model-requestable operations."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIRECTORY = "list_directory"
    EXECUTE = "execute"
    SEARCH_FILES = "search_files"


class WriteMode(str, Enum):
    CREATE = "create"
    OVERWRITE = "overwrite"
    APPEND = "append"


@dataclass
class ActionRequest:
    action_type: ActionType
    params: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


@dataclass
class ActionResult:
    action_type: ActionType
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_context_string(self) -> str:
        """Render result as text to feed back to the model."""
        if not self.success:
            return f"[{self.action_type.value} FAILED]: {self.error}"
        match self.action_type:
            case ActionType.READ_FILE:
                content = self.data.get("content", "")
                path = self.data.get("path", "")
                return f"[read_file result: {path}]\n{content}\n[end of file]"
            case ActionType.WRITE_FILE:
                return f"[write_file result]: File written successfully to {self.data.get('path', '')}"
            case ActionType.LIST_DIRECTORY:
                entries = self.data.get("entries", [])
                path = self.data.get("path", "")
                listing = "\n".join(entries)
                return f"[list_directory result: {path}]\n{listing}"
            case ActionType.EXECUTE:
                stdout = self.data.get("stdout", "")
                stderr = self.data.get("stderr", "")
                exit_code = self.data.get("exit_code", 0)
                parts = [f"[execute result] exit_code={exit_code}"]
                if stdout:
                    parts.append(f"stdout:\n{stdout}")
                if stderr:
                    parts.append(f"stderr:\n{stderr}")
                return "\n".join(parts)
            case ActionType.SEARCH_FILES:
                matches = self.data.get("matches", [])
                return f"[search_files result]: {len(matches)} match(es)\n" + "\n".join(matches)
            case _:
                return f"[{self.action_type.value} result]: {self.data}"


# Native tool schemas — used by drivers that support function/tool calling.
NATIVE_TOOL_SCHEMAS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file at the given path.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file."},
                "content": {"type": "string", "description": "Content to write."},
                "mode": {
                    "type": "string",
                    "enum": ["create", "overwrite", "append"],
                    "description": "Write mode. 'create' fails if file exists.",
                },
            },
            "required": ["path", "content", "mode"],
        },
    },
    {
        "name": "list_directory",
        "description": "List entries in a directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list."},
                "recursive": {"type": "boolean", "description": "Whether to list recursively."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "execute",
        "description": "Execute a shell command.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run."},
                "working_dir": {"type": "string", "description": "Working directory (optional)."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for files matching a pattern.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern."},
                "path": {"type": "string", "description": "Directory to search in."},
                "type": {"type": "string", "enum": ["glob", "regex"], "description": "Pattern type."},
            },
            "required": ["pattern", "path"],
        },
    },
]
