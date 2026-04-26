"""Tests focused on path permission enforcement — especially symlink traversal."""

import os
from pathlib import Path

import pytest
from aicli.core.actions import ActionRequest, ActionType, WriteMode
from aicli.core.executor import Executor
from aicli.core.executor import PermissionError as ExecPermissionError


def test_symlink_outside_allowed_blocked(tmp_path, tmp_path_factory):
    """A symlink pointing outside the allowed dir must be blocked on write."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path_factory.mktemp("outside")
    link = allowed / "escape.txt"
    target = outside / "secret.txt"
    target.write_text("secret")
    link.symlink_to(target)

    ex = Executor(allowed_dirs=[str(allowed)])
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(link),
        "content": "pwned",
        "mode": WriteMode.OVERWRITE,
    })
    # Resolved path of link is outside allowed; must raise.
    with pytest.raises(ExecPermissionError):
        ex.execute(req)


def test_path_traversal_blocked(tmp_path, tmp_path_factory):
    """../../ style traversal outside allowed dir must be blocked."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path_factory.mktemp("outside")
    traversal = str(allowed) + "/../../" + outside.name + "/evil.txt"

    ex = Executor(allowed_dirs=[str(allowed)])
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": traversal,
        "content": "evil",
        "mode": WriteMode.CREATE,
    })
    with pytest.raises(ExecPermissionError):
        ex.execute(req)


def test_write_to_subdirectory_within_allowed(tmp_path):
    """Writes into subdirectories of allowed dirs should succeed."""
    allowed = tmp_path / "project"
    allowed.mkdir()
    sub = allowed / "charts" / "output"
    ex = Executor(allowed_dirs=[str(allowed)])
    req = ActionRequest(ActionType.WRITE_FILE, {
        "path": str(sub / "result.txt"),
        "content": "ok",
        "mode": WriteMode.OVERWRITE,
    })
    result = ex.execute(req)
    assert result.success


def test_multiple_allowed_dirs(tmp_path, tmp_path_factory):
    """A write into any one of multiple allowed dirs should succeed."""
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    dir_b = tmp_path_factory.mktemp("b")

    ex = Executor(allowed_dirs=[str(dir_a), str(dir_b)])
    for target_dir in (dir_a, dir_b):
        req = ActionRequest(ActionType.WRITE_FILE, {
            "path": str(target_dir / "out.txt"),
            "content": "data",
            "mode": WriteMode.OVERWRITE,
        })
        result = ex.execute(req)
        assert result.success
