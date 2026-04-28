"""Abstract driver interface — every vendor driver must implement this."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator


@dataclass
class NativeToolCall:
    """A tool call returned by the model via native function-calling."""
    name: str
    params: dict
    call_id: str = ""


@dataclass
class ResponseChunk:
    """A single chunk from the streaming response.

    When done=True, this is the terminal chunk and carries metadata plus any
    native tool calls. Text chunks have done=False and non-empty text.
    """
    text: str = ""
    done: bool = False
    native_tool_calls: list[NativeToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = ""


class BaseDriver(ABC):
    @abstractmethod
    def configure(
        self,
        api_base: str,
        api_key: str | None,
        model: str,
        options: dict | None = None,
    ) -> None: ...

    @abstractmethod
    def send(
        self,
        messages: list[dict],
        system_prompt: str = "",
        stream: bool = True,
        use_tools: bool = True,
    ) -> Generator[ResponseChunk, None, None]:
        """Yield ResponseChunk objects. The final chunk has done=True and may
        carry native_tool_calls, token counts, and model metadata.

        Pass use_tools=False to suppress native tool schemas (V2 planner mode).
        """
        ...

    @abstractmethod
    def list_models(self) -> list[str]: ...

    @abstractmethod
    def supports_native_tools(self) -> bool: ...

    def get_native_tool_schema(self) -> list[dict] | None:
        return None
