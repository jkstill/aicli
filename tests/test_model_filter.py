"""Tests for model exclusion filtering."""

from aicli.config import filter_models

_MODELS = [
    "glm-4.7-flash:latest",
    "qwen3.5:latest",
    "qwen3-coder:30b",
    "mxbai-embed-large:latest",
    "nomic-embed-text:latest",
    "llama3:latest",
    "llama3.1:latest",
    "llama3.2:latest",
    "llava:7b",
    "gemma3:12b",
    "batiai/qwen3.6-35b:q3",
]


def _cfg(*patterns):
    return {"model_exclusions": list(patterns)}


def test_no_patterns_returns_all():
    assert filter_models(_MODELS, {}) == _MODELS


def test_embed_wildcard_removes_embedding_models():
    result = filter_models(_MODELS, _cfg("*embed*"))
    assert "mxbai-embed-large:latest" not in result
    assert "nomic-embed-text:latest" not in result
    assert "glm-4.7-flash:latest" in result


def test_llama3_colon_star_removes_only_exact_llama3():
    result = filter_models(_MODELS, _cfg("llama3:*"))
    assert "llama3:latest" not in result
    # llama3.1 and llama3.2 are different model families — not excluded
    assert "llama3.1:latest" in result
    assert "llama3.2:latest" in result


def test_multiple_patterns():
    result = filter_models(_MODELS, _cfg("*embed*", "llama3:*", "llava*"))
    assert "mxbai-embed-large:latest" not in result
    assert "llama3:latest" not in result
    assert "llava:7b" not in result
    assert "glm-4.7-flash:latest" in result
    assert "qwen3.5:latest" in result


def test_pattern_matching_is_case_insensitive():
    result = filter_models(["MXBai-Embed-Large:latest"], _cfg("*embed*"))
    assert result == []


def test_default_exclusions_applied(monkeypatch):
    from aicli.config import _DEFAULTS
    result = filter_models(_MODELS, _DEFAULTS)
    assert "mxbai-embed-large:latest" not in result
    assert "nomic-embed-text:latest" not in result
    assert "llama3:latest" not in result
    # llama3.2 is NOT in default exclusions
    assert "llama3.2:latest" in result


def test_empty_model_list():
    assert filter_models([], _cfg("*embed*")) == []
