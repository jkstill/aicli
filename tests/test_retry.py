"""Tests for the tool-call retry nudge mechanism (V1 only).

_prompt_implies_tool_use was removed in the V2 rewrite. Tests that relied on it
are skipped. The V1 system_prompt constants are still present and tested.
"""

import pytest
from aicli.core.system_prompt import TOOL_RETRY_NUDGE, NATIVE_TOOLS_HINT


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


@pytest.mark.skip(reason="_prompt_implies_tool_use removed in V2")
@pytest.mark.parametrize("prompt", [
    "Write a file called output.txt with 'hello'",
    "Save the results to results.json",
])
def test_prompt_implies_tool_use_true(prompt):
    pass


@pytest.mark.skip(reason="_prompt_implies_tool_use removed in V2")
@pytest.mark.parametrize("prompt", [
    "Explain what a gnuplot script is in two sentences.",
    "What is a Makefile?",
])
def test_prompt_implies_tool_use_false(prompt):
    pass
