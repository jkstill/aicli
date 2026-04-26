"""Conversation history and multi-turn state."""

from dataclasses import dataclass, field


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool" | "system"
    content: str


@dataclass
class Session:
    messages: list[Message] = field(default_factory=list)
    system_prompt: str = ""

    def add_user(self, content: str) -> None:
        self.messages.append(Message(role="user", content=content))

    def add_assistant(self, content: str) -> None:
        self.messages.append(Message(role="assistant", content=content))

    def add_tool_result(self, content: str) -> None:
        """Feed action results back as a user-turn so the model sees them."""
        self.messages.append(Message(role="user", content=content))

    def as_ollama_messages(self) -> list[dict]:
        return [{"role": m.role if m.role != "tool" else "user", "content": m.content}
                for m in self.messages]
