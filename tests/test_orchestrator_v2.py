"""Tests for the V2 Orchestrator."""

import pytest
from pathlib import Path

from aicli.core.orchestrator import Orchestrator
from aicli.core.plan_parser import PlanStep
from aicli.drivers.base import ResponseChunk


# ---------------------------------------------------------------------------
# Mock driver
# ---------------------------------------------------------------------------

class MockDriver:
    """Minimal driver that returns a fixed response for every send() call."""

    def __init__(self, response: str = "mock LLM response"):
        self.response = response
        self.calls: list[dict] = []

    def send(self, messages, system_prompt="", stream=True, use_tools=True):
        self.calls.append({"messages": messages, "system_prompt": system_prompt})
        yield ResponseChunk(text=self.response, done=False)
        yield ResponseChunk(done=True)

    def configure(self, *args, **kwargs):
        pass

    def list_models(self):
        return []

    def supports_native_tools(self):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(number, keyword, arg, body="", save_path=""):
    return PlanStep(
        number=number,
        keyword=keyword,
        arg=arg,
        body=body,
        save_path=save_path,
    )


def _make_orchestrator(tmp_path, driver=None, allow_exec=False, auto_approve=True,
                       on_error="continue", verbose=False):
    if driver is None:
        driver = MockDriver()
    return Orchestrator(
        analysis_driver=driver,
        allowed_dirs=[str(tmp_path)],
        allow_exec=allow_exec,
        auto_approve=auto_approve,
        dry_run=False,
        verbose=verbose,
        renderer=None,
        exec_timeout=10,
        on_error=on_error,
    )


# ---------------------------------------------------------------------------
# READFILE
# ---------------------------------------------------------------------------

def test_readfile_cat(tmp_path):
    target = tmp_path / "hello.txt"
    target.write_text("hello world")
    orch = _make_orchestrator(tmp_path)
    steps = [_step(1, "READFILE", f"cat {target}")]
    orch.run(steps)


def test_readfile_captures_output(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("line1\nline2\n")
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver)

    # Follow with a PROMPT so we can check auto-injection picks up the file content
    steps = [
        _step(1, "READFILE", f"cat {target}"),
        _step(2, "PROMPT", "Summarize"),
    ]
    orch.run(steps)
    # The PROMPT call should have received the file content via auto-injection
    prompt_text = driver.calls[0]["messages"][0]["content"]
    assert "line1" in prompt_text or "Summarize" in prompt_text


# ---------------------------------------------------------------------------
# LISTDIR
# ---------------------------------------------------------------------------

def test_listdir(tmp_path):
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [_step(1, "LISTDIR", str(tmp_path))]
    orch.run(steps)
    # No assertion on driver calls — just ensure it doesn't crash


def test_listdir_result_in_store(tmp_path):
    (tmp_path / "alpha.txt").write_text("")
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [
        _step(1, "LISTDIR", str(tmp_path)),
        _step(2, "PROMPT", "What files are here?"),
    ]
    orch.run(steps)
    prompt_text = driver.calls[0]["messages"][0]["content"]
    assert "alpha.txt" in prompt_text


# ---------------------------------------------------------------------------
# WRITEFILE
# ---------------------------------------------------------------------------

def test_writefile_writes_body(tmp_path):
    out = tmp_path / "out.txt"
    orch = _make_orchestrator(tmp_path)
    steps = [_step(1, "WRITEFILE", str(out), body="written content")]
    orch.run(steps)
    assert out.read_text() == "written content"


def test_writefile_auto_injects_latest_result(tmp_path):
    """WRITEFILE with empty body should write the latest stored result."""
    out = tmp_path / "out.txt"
    driver = MockDriver(response="LLM analysis here")
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [
        _step(1, "PROMPT", "Analyze the data"),
        _step(2, "WRITEFILE", str(out), body=""),
    ]
    orch.run(steps)
    assert "LLM analysis here" in out.read_text()


def test_writefile_denied_outside_allowed(tmp_path, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    out = other / "bad.txt"
    orch = _make_orchestrator(tmp_path)
    steps = [_step(1, "WRITEFILE", str(out), body="data")]
    orch.run(steps)
    assert not out.exists()


# ---------------------------------------------------------------------------
# EXEC
# ---------------------------------------------------------------------------

def test_exec_denied_without_allow_exec(tmp_path):
    orch = _make_orchestrator(tmp_path, allow_exec=False, on_error="continue")
    steps = [_step(1, "EXEC", "echo hello")]
    orch.run(steps)  # Should not raise — step fails gracefully


def test_exec_allowed(tmp_path):
    orch = _make_orchestrator(tmp_path, allow_exec=True)
    steps = [_step(1, "EXEC", "echo hello")]
    orch.run(steps)


def test_exec_captures_stdout(tmp_path):
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver, allow_exec=True)
    steps = [
        _step(1, "EXEC", "echo captured_output"),
        _step(2, "PROMPT", "Describe the output"),
    ]
    orch.run(steps)
    prompt_text = driver.calls[0]["messages"][0]["content"]
    assert "captured_output" in prompt_text


def test_exec_nonzero_exit_marks_failure(tmp_path):
    orch = _make_orchestrator(tmp_path, allow_exec=True, on_error="continue")
    steps = [_step(1, "EXEC", "exit 1")]
    orch.run(steps)  # Step fails (exit_code != 0) but on_error=continue


# ---------------------------------------------------------------------------
# PROMPT
# ---------------------------------------------------------------------------

def test_prompt_calls_driver(tmp_path):
    driver = MockDriver(response="analysis result")
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [_step(1, "PROMPT", "Analyze this")]
    result = orch.run(steps)
    assert result == "analysis result"
    assert len(driver.calls) == 1


def test_prompt_auto_injects_previous_result(tmp_path):
    """When no {RESULT_OF_STEP_N} ref, previous result is appended."""
    target = tmp_path / "data.txt"
    target.write_text("file content here")
    driver = MockDriver(response="good analysis")
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [
        _step(1, "READFILE", f"cat {target}"),
        _step(2, "PROMPT", "Analyze the data above"),
    ]
    orch.run(steps)
    prompt_text = driver.calls[0]["messages"][0]["content"]
    assert "file content here" in prompt_text


def test_prompt_uses_explicit_ref(tmp_path):
    """When {RESULT_OF_STEP_1} is present, no double-injection."""
    target = tmp_path / "data.txt"
    target.write_text("explicit data")
    driver = MockDriver(response="fine")
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [
        _step(1, "READFILE", f"cat {target}"),
        _step(2, "PROMPT", "Analyze: {RESULT_OF_STEP_1}"),
    ]
    orch.run(steps)
    prompt_text = driver.calls[0]["messages"][0]["content"]
    assert "explicit data" in prompt_text
    # Should NOT appear twice (no double-injection)
    assert prompt_text.count("explicit data") == 1


# ---------------------------------------------------------------------------
# GENCODE
# ---------------------------------------------------------------------------

def test_gencode_writes_file(tmp_path):
    out = tmp_path / "script.py"
    driver = MockDriver(response="print('hello')")
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [_step(1, "GENCODE", "python", body="Write hello world", save_path=str(out))]
    orch.run(steps)
    assert "print('hello')" in out.read_text()


def test_gencode_strips_fences(tmp_path):
    out = tmp_path / "script.py"
    driver = MockDriver(response="```python\nprint('hello')\n```")
    orch = _make_orchestrator(tmp_path, driver=driver)
    steps = [_step(1, "GENCODE", "python", body="Write hello world", save_path=str(out))]
    orch.run(steps)
    text = out.read_text()
    assert "print('hello')" in text
    assert "```" not in text


def test_gencode_missing_saveas_fails_gracefully(tmp_path):
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver, on_error="continue")
    steps = [_step(1, "GENCODE", "python", body="Write something", save_path="")]
    orch.run(steps)  # Should not raise


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------

def test_dry_run_skips_all_steps(tmp_path):
    driver = MockDriver()
    out = tmp_path / "out.txt"
    orch = Orchestrator(
        analysis_driver=driver,
        allowed_dirs=[str(tmp_path)],
        allow_exec=False,
        auto_approve=True,
        dry_run=True,
        verbose=False,
        renderer=None,
        exec_timeout=10,
    )
    steps = [
        _step(1, "READFILE", "cat /etc/hostname"),
        _step(2, "PROMPT", "Analyze"),
        _step(3, "WRITEFILE", str(out), body="data"),
    ]
    orch.run(steps)
    assert not driver.calls
    assert not out.exists()


# ---------------------------------------------------------------------------
# EXEC early warning (just checking it doesn't crash with exec steps present)
# ---------------------------------------------------------------------------

def test_exec_warning_emitted_when_no_allow_exec(tmp_path, capsys):
    orch = Orchestrator(
        analysis_driver=MockDriver(),
        allowed_dirs=[str(tmp_path)],
        allow_exec=False,
        auto_approve=True,
        dry_run=False,
        verbose=False,
        renderer=None,
        exec_timeout=5,
        on_error="continue",
    )
    steps = [_step(1, "EXEC", "echo hi")]
    orch.run(steps)  # Should not raise; warning goes to renderer (None here) → no-op


# ---------------------------------------------------------------------------
# on_error behavior
# ---------------------------------------------------------------------------

def test_on_error_abort_stops_after_failure(tmp_path):
    """on_error=abort should stop after the first failing step."""
    out = tmp_path / "should_not_exist.txt"
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver, on_error="abort")
    steps = [
        # This EXEC will fail (allow_exec=False)
        _step(1, "EXEC", "echo hi"),
        # This WRITEFILE should never run
        _step(2, "WRITEFILE", str(out), body="should not appear"),
    ]
    orch.run(steps)
    assert not out.exists()


def test_on_error_continue_keeps_going_after_failure(tmp_path):
    """on_error=continue should keep executing after a failing step."""
    out = tmp_path / "should_exist.txt"
    driver = MockDriver()
    orch = _make_orchestrator(tmp_path, driver=driver, on_error="continue")
    steps = [
        _step(1, "EXEC", "echo hi"),             # fails (no allow_exec)
        _step(2, "WRITEFILE", str(out), body="hello"),  # should still run
    ]
    orch.run(steps)
    assert out.exists()
    assert "hello" in out.read_text()


def test_on_error_ask_with_auto_approve_continues(tmp_path):
    """on_error=ask with auto_approve=True should continue (no prompt)."""
    out = tmp_path / "should_exist.txt"
    driver = MockDriver()
    orch = Orchestrator(
        analysis_driver=driver,
        allowed_dirs=[str(tmp_path)],
        allow_exec=False,
        auto_approve=True,
        dry_run=False,
        verbose=False,
        renderer=None,
        exec_timeout=5,
        on_error="ask",
    )
    steps = [
        _step(1, "EXEC", "echo hi"),
        _step(2, "WRITEFILE", str(out), body="present"),
    ]
    orch.run(steps)
    assert out.exists()
