"""Action execution engine with permission enforcement.

The executor is completely isolated from drivers. It receives ActionRequests,
enforces permissions, and returns ActionResults. It never talks to a model.
"""

import fnmatch
import glob
import os
import re
import subprocess
from pathlib import Path

from .actions import ActionRequest, ActionResult, ActionType, WriteMode


class PermissionError(Exception):
    pass


class Executor:
    def __init__(
        self,
        allowed_dirs: list[str] | None = None,
        allow_exec: bool = False,
    ):
        self.allowed_dirs: list[Path] = [Path(d).resolve() for d in (allowed_dirs or [])]
        self.allow_exec = allow_exec

    # ------------------------------------------------------------------
    # Permission checks
    # ------------------------------------------------------------------

    def _resolve_and_check(self, path_str: str, write: bool = False) -> Path:
        """Resolve path and verify it sits inside an allowed directory."""
        path = Path(path_str).resolve()

        # Reads are always permitted (no directory restriction for reads).
        if not write:
            return path

        # Writes require the path to be inside an allowed directory.
        if not self.allowed_dirs:
            raise PermissionError(
                f"Write to '{path}' denied: no --include-directories specified."
            )

        for allowed in self.allowed_dirs:
            try:
                path.relative_to(allowed)
                return path
            except ValueError:
                continue

        allowed_str = ", ".join(str(d) for d in self.allowed_dirs)
        raise PermissionError(
            f"Write to '{path}' denied: not inside allowed directories [{allowed_str}]."
        )

    # ------------------------------------------------------------------
    # Individual action handlers
    # ------------------------------------------------------------------

    def _read_file(self, req: ActionRequest) -> ActionResult:
        path = self._resolve_and_check(req.get("path"), write=False)
        try:
            content = path.read_text(errors="replace")
            return ActionResult(
                action_type=ActionType.READ_FILE,
                success=True,
                data={"path": str(path), "content": content},
            )
        except OSError as e:
            return ActionResult(ActionType.READ_FILE, success=False, error=str(e))

    def _write_file(self, req: ActionRequest) -> ActionResult:
        path = self._resolve_and_check(req.get("path"), write=True)
        mode: WriteMode = req.get("mode", WriteMode.OVERWRITE)
        content: str = req.get("content", "")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if mode == WriteMode.CREATE and path.exists():
                return ActionResult(
                    ActionType.WRITE_FILE,
                    success=False,
                    error=f"File '{path}' already exists (mode=create).",
                )
            if mode == WriteMode.APPEND:
                with path.open("a") as fh:
                    fh.write(content)
            else:
                path.write_text(content)
            return ActionResult(
                ActionType.WRITE_FILE,
                success=True,
                data={"path": str(path)},
            )
        except OSError as e:
            return ActionResult(ActionType.WRITE_FILE, success=False, error=str(e))

    def _list_directory(self, req: ActionRequest) -> ActionResult:
        path = self._resolve_and_check(req.get("path"), write=False)
        recursive: bool = req.get("recursive", False)
        try:
            if not path.is_dir():
                return ActionResult(
                    ActionType.LIST_DIRECTORY,
                    success=False,
                    error=f"'{path}' is not a directory.",
                )
            if recursive:
                entries = [
                    str(p.relative_to(path))
                    for p in sorted(path.rglob("*"))
                ]
            else:
                entries = sorted(p.name + ("/" if p.is_dir() else "") for p in path.iterdir())
            return ActionResult(
                ActionType.LIST_DIRECTORY,
                success=True,
                data={"path": str(path), "entries": entries},
            )
        except OSError as e:
            return ActionResult(ActionType.LIST_DIRECTORY, success=False, error=str(e))

    def _execute(self, req: ActionRequest) -> ActionResult:
        if not self.allow_exec:
            return ActionResult(
                ActionType.EXECUTE,
                success=False,
                error="Command execution denied: --allow-exec not set.",
            )
        command: str = req.get("command", "")
        working_dir: str | None = req.get("working_dir")
        timeout: int = req.get("timeout", 30)
        cwd = Path(working_dir).resolve() if working_dir else None
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            return ActionResult(
                ActionType.EXECUTE,
                success=True,
                data={
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "exit_code": result.returncode,
                },
            )
        except subprocess.TimeoutExpired:
            return ActionResult(ActionType.EXECUTE, success=False, error=f"Command timed out after {timeout}s.")
        except OSError as e:
            return ActionResult(ActionType.EXECUTE, success=False, error=str(e))

    def _search_files(self, req: ActionRequest) -> ActionResult:
        pattern: str = req.get("pattern", "")
        search_path: str = req.get("path", ".")
        pattern_type: str = req.get("type", "glob")
        root = self._resolve_and_check(search_path, write=False)
        try:
            matches: list[str] = []
            if pattern_type == "glob":
                matches = sorted(str(p) for p in root.rglob(pattern))
            else:
                rx = re.compile(pattern)
                for p in sorted(root.rglob("*")):
                    if rx.search(p.name):
                        matches.append(str(p))
            return ActionResult(
                ActionType.SEARCH_FILES,
                success=True,
                data={"matches": matches},
            )
        except (OSError, re.error) as e:
            return ActionResult(ActionType.SEARCH_FILES, success=False, error=str(e))

    # ------------------------------------------------------------------
    # Public dispatch
    # ------------------------------------------------------------------

    def execute(self, req: ActionRequest) -> ActionResult:
        match req.action_type:
            case ActionType.READ_FILE:
                return self._read_file(req)
            case ActionType.WRITE_FILE:
                return self._write_file(req)
            case ActionType.LIST_DIRECTORY:
                return self._list_directory(req)
            case ActionType.EXECUTE:
                return self._execute(req)
            case ActionType.SEARCH_FILES:
                return self._search_files(req)
            case _:
                return ActionResult(
                    req.action_type,
                    success=False,
                    error=f"Unknown action type: {req.action_type}",
                )
