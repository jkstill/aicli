"""Tests for CLI utility functions."""

import pytest
from aicli.cli import _parse_model


def test_parse_model_no_slash():
    assert _parse_model("qwen3.5:latest") == ("ollama", "qwen3.5:latest")


def test_parse_model_known_driver_prefix():
    assert _parse_model("ollama/qwen3.5:latest") == ("ollama", "qwen3.5:latest")
    assert _parse_model("claude/claude-3-5-sonnet") == ("claude", "claude-3-5-sonnet")
    assert _parse_model("openai/gpt-4o") == ("openai", "gpt-4o")
    assert _parse_model("gemini/gemini-pro") == ("gemini", "gemini-pro")


def test_parse_model_unknown_prefix_treated_as_ollama_namespace():
    # batiai/ is an Ollama Hub namespace, not a driver — whole string is the model name.
    assert _parse_model("batiai/qwen3.6-35b:q3") == ("ollama", "batiai/qwen3.6-35b:q3")
    assert _parse_model("namespace/some-model:tag") == ("ollama", "namespace/some-model:tag")
