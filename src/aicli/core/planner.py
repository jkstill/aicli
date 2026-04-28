"""Planner: combines the system prompt + user task, sends to LLM, returns the plan."""

from pathlib import Path
from typing import Callable

_DEFAULT_PROMPT = Path(__file__).parent / "system_prompts" / "default.md"


def load_system_prompt(override_path: str | None = None) -> str:
    if override_path:
        return Path(override_path).read_text()
    return _DEFAULT_PROMPT.read_text()


class Planner:
    def __init__(self, driver, system_prompt: str) -> None:
        self._driver = driver
        self._system_prompt = system_prompt

    def get_plan(
        self,
        task: str,
        stream_callback: Callable[[str], None] | None = None,
    ) -> str:
        """Send the task to the LLM with the planner system prompt.

        Returns the full plan text. If stream_callback is provided, each text
        chunk is forwarded to it as it arrives.
        """
        content = (
            "Produce a step plan using ONLY step blocks "
            "(READFILE, WRITEFILE, LISTDIR, EXEC, PROMPT, GENCODE). "
            "No prose. No explanations.\n\n"
            f"TASK: {task}"
        )
        messages = [{"role": "user", "content": content}]
        text = ""
        for chunk in self._driver.send(
            messages,
            system_prompt=self._system_prompt,
            stream=True,
            use_tools=False,
        ):
            if chunk.done:
                break
            if chunk.text:
                text += chunk.text
                if stream_callback:
                    stream_callback(chunk.text)
        return text
