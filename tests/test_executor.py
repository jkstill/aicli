"""Tests for the action executor."""

import os
import tempfile
from pathlib import Path

import pytest
from aicli.core.actions import ActionRequest, ActionType, WriteMode
from aicli.core.executor import Executor
from aicli.core.executor import PermissionError as ExecPermissionError


@pytest.fixture
def tmpdir_path(tmp_path):
    return tmp_path


@pytest.fixture
def executor_with_tmpdir(tmpdir_path):
    return Executor(allowed_dirs=[str(tmpdir_path)], allow_exec=True)


@pytest.fixture
def readonly_executor():
    return Executor(allowed_dirs=[], allow_exec=False)


# --- read_file ---

def test_read_file_existing(tmp_path, readonly_executor):
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    req = ActionRequest(ActionType.READ_FILE, {"path": str(f)})
    result = readonly_executor.execute(req)
    assert result.success
    assert "hello world" in result.data["content"]


def test_read_file_missing(readonly_executor):
    req = ActionRequest(ActionType.READ_FILE, {"path": "/nonexistent/path/xyz.txt"})
    result = readonly_executor.execute(req)
    assert not result.success
    assert result.error


# --- write_file ---

def test_write_file_allowed(tmp_path):
    ex = Executor(allowed_dirs=[str(tmp_path)])
    target = tmp_path / "output.txt"
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(target),
        "content": "test content",
        "mode": WriteMode.OVERWRITE,
    })
    result = ex.execute(req)
    assert result.success
    assert target.read_text() == "test content"


def test_write_file_denied_no_dirs(tmp_path):
    ex = Executor(allowed_dirs=[])
    target = tmp_path / "output.txt"
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(target),
        "content": "data",
        "mode": WriteMode.OVERWRITE,
    })
    with pytest.raises(ExecPermissionError):
        ex.execute(req)


def test_write_file_denied_outside_allowed(tmp_path, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    ex = Executor(allowed_dirs=[str(tmp_path)])
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(other / "bad.txt"),
        "content": "data",
        "mode": WriteMode.OVERWRITE,
    })
    with pytest.raises(ExecPermissionError):
        ex.execute(req)


def test_write_file_create_fails_if_exists(tmp_path):
    ex = Executor(allowed_dirs=[str(tmp_path)])
    target = tmp_path / "existing.txt"
    target.write_text("original")
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(target),
        "content": "new",
        "mode": WriteMode.CREATE,
    })
    result = ex.execute(req)
    assert not result.success
    assert "already exists" in result.error


def test_write_file_append(tmp_path):
    ex = Executor(allowed_dirs=[str(tmp_path)])
    target = tmp_path / "append.txt"
    target.write_text("line1\n")
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(target),
        "content": "line2\n",
        "mode": WriteMode.APPEND,
    })
    result = ex.execute(req)
    assert result.success
    assert target.read_text() == "line1\nline2\n"


# --- list_directory ---

def test_list_directory(tmp_path, readonly_executor):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    req = ActionRequest(ActionType.LIST_DIRECTORY, {"path": str(tmp_path), "recursive": False})
    result = readonly_executor.execute(req)
    assert result.success
    assert "a.txt" in result.data["entries"]
    assert "b.txt" in result.data["entries"]


def test_list_directory_recursive(tmp_path, readonly_executor):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("x")
    req = ActionRequest(ActionType.LIST_DIRECTORY, {"path": str(tmp_path), "recursive": True})
    result = readonly_executor.execute(req)
    assert result.success
    joined = " ".join(result.data["entries"])
    assert "nested.txt" in joined


# --- execute ---

def test_execute_allowed(executor_with_tmpdir):
    req = ActionRequest(ActionType.EXECUTE, {"command": "echo hello", "working_dir": None, "timeout": 10})
    result = executor_with_tmpdir.execute(req)
    assert result.success
    assert "hello" in result.data["stdout"]


def test_execute_denied(readonly_executor):
    req = ActionRequest(ActionType.EXECUTE, {"command": "echo hi", "working_dir": None, "timeout": 5})
    result = readonly_executor.execute(req)
    assert not result.success
    assert "allow-exec" in result.error.lower() or "denied" in result.error.lower()


def test_execute_exit_code(executor_with_tmpdir):
    req = ActionRequest(ActionType.EXECUTE, {"command": "exit 42", "working_dir": None, "timeout": 5})
    result = executor_with_tmpdir.execute(req)
    assert result.success
    assert result.data["exit_code"] == 42


# --- search_files ---

def test_search_files_glob(tmp_path, readonly_executor):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    req = ActionRequest(ActionType.SEARCH_FILES, {
        "pattern": "*.py", "path": str(tmp_path), "type": "glob",
    })
    result = readonly_executor.execute(req)
    assert result.success
    assert len(result.data["matches"]) == 2


def test_search_files_regex(tmp_path, readonly_executor):
    (tmp_path / "foo_test.py").write_text("")
    (tmp_path / "bar.py").write_text("")
    req = ActionRequest(ActionType.SEARCH_FILES, {
        "pattern": r"test", "path": str(tmp_path), "type": "regex",
    })
    result = readonly_executor.execute(req)
    assert result.success
    assert any("foo_test" in m for m in result.data["matches"])


# --- None / empty path guard ---

def test_write_file_none_path_raises(executor_with_tmpdir):
    req = ActionRequest(ActionType.WRITE_FILE, {"path": None, "content": "x", "mode": WriteMode.OVERWRITE})
    with pytest.raises(ExecPermissionError, match="path is empty"):
        executor_with_tmpdir.execute(req)


def test_read_file_none_path_raises(readonly_executor):
    req = ActionRequest(ActionType.READ_FILE, {"path": None})
    with pytest.raises(ExecPermissionError, match="path is empty"):
        readonly_executor.execute(req)
