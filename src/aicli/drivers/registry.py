"""Driver registry — maps driver name strings to driver classes."""

from .base import BaseDriver
from .ollama import OllamaDriver
from .gemini import GeminiDriver
from .claude import ClaudeDriver
from .openai import OpenAIDriver

_REGISTRY: dict[str, type[BaseDriver]] = {
    "ollama": OllamaDriver,
    "gemini": GeminiDriver,
    "claude": ClaudeDriver,
    "openai": OpenAIDriver,
}


def get_driver(name: str) -> BaseDriver:
    cls = _REGISTRY.get(name.lower())
    if cls is None:
        available = ", ".join(_REGISTRY)
        raise ValueError(f"Unknown driver '{name}'. Available: {available}")
    return cls()


def list_drivers() -> list[str]:
    return list(_REGISTRY)
