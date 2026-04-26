"""Claude (Anthropic) driver — placeholder for Phase 3."""

from typing import Generator
from .base import BaseDriver, ResponseChunk


class ClaudeDriver(BaseDriver):
    def configure(self, api_base, api_key, model, options=None):
        raise NotImplementedError("Claude driver not yet implemented (Phase 3).")

    def send(self, messages, system_prompt="", stream=True) -> Generator[ResponseChunk, None, None]:
        raise NotImplementedError

    def list_models(self):
        raise NotImplementedError

    def supports_native_tools(self):
        return True
