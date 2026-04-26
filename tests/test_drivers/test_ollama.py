"""Integration tests for the Ollama driver — requires a running Ollama at lestrade:11434."""

import pytest
from aicli.drivers.ollama import OllamaDriver

OLLAMA_BASE = "http://lestrade:11434"
# Small fast model for integration tests
TEST_MODEL = "qwen3.5:latest"


@pytest.fixture(scope="module")
def driver():
    d = OllamaDriver()
    d.configure(api_base=OLLAMA_BASE, api_key=None, model=TEST_MODEL)
    return d


def test_list_models(driver):
    models = driver.list_models()
    assert isinstance(models, list)
    assert len(models) > 0
    assert all(isinstance(m, str) for m in models)


def test_supports_native_tools_returns_bool(driver):
    result = driver.supports_native_tools()
    assert isinstance(result, bool)


def test_simple_stream(driver):
    messages = [{"role": "user", "content": "Say exactly: PONG"}]
    chunks = []
    done_chunk = None
    for chunk in driver.send(messages, stream=True):
        if chunk.done:
            done_chunk = chunk
        else:
            chunks.append(chunk.text)

    full_text = "".join(chunks)
    assert len(full_text) > 0
    assert done_chunk is not None
    assert done_chunk.done is True


def test_system_prompt_injected(driver):
    messages = [{"role": "user", "content": "What is 2+2?"}]
    system = "Always respond with only the number, nothing else."
    chunks = []
    for chunk in driver.send(messages, system_prompt=system, stream=True):
        if not chunk.done:
            chunks.append(chunk.text)
    full_text = "".join(chunks)
    assert len(full_text) > 0
