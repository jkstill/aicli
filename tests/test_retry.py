"""Tests for the tool-call retry nudge mechanism."""

import pytest
from aicli.core.system_prompt import TOOL_RETRY_NUDGE, NATIVE_TOOLS_HINT
from aicli.cli import _prompt_implies_tool_use


def test_tool_retry_nudge_exists():
    assert TOOL_RETRY_NUDGE
    assert len(TOOL_RETRY_NUDGE) > 20


def test_native_tools_hint_lists_tools():
    for tool in ("write_file", "read_file", "execute", "list_directory", "search_files"):
        assert tool in NATIVE_TOOLS_HINT, f"Tool '{tool}' missing from NATIVE_TOOLS_HINT"


def test_native_tools_hint_has_rules():
    assert "NEVER" in NATIVE_TOOLS_HINT or "never" in NATIVE_TOOLS_HINT.lower()


def test_retry_nudge_mentions_tools():
    for tool in ("write_file", "read_file", "execute"):
        assert tool in TOOL_RETRY_NUDGE, f"Tool '{tool}' not mentioned in retry nudge"


# --- _prompt_implies_tool_use ---

@pytest.mark.parametrize("prompt", [
    "Write a file called output.txt with 'hello'",
    "Save the results to results.json",
    "Create a new directory called logs",
    "Read the contents of config.yaml",
    "Run the test suite",
    "Execute the migration script",
    "List the files in /tmp",
    "Find all Python files under src/",
    "Delete the temp files",
    "Search for TODO comments",
    "Move old_name.txt to new_name.txt",
    "Open /etc/hosts and show its contents",
])
def test_prompt_implies_tool_use_true(prompt):
    assert _prompt_implies_tool_use(prompt), f"Expected True for: {prompt!r}"


@pytest.mark.parametrize("prompt", [
    "Explain what a gnuplot script is in two sentences.",
    "What is a Makefile?",
    "Describe the difference between TCP and UDP.",
    "How does a hash table work?",
    "Tell me about Python decorators.",
    "What does 'idempotent' mean?",
    "Summarize the history of Unix.",
])
def test_prompt_implies_tool_use_false(prompt):
    assert not _prompt_implies_tool_use(prompt), f"Expected False for: {prompt!r}"
