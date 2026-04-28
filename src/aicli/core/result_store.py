"""Result store for the V2 planner/executor pipeline.

Stores step outputs keyed by step number and substitutes
{RESULT_OF_STEP_N} and {RESULT_OF_PREVIOUS_STEP} placeholders.
"""

import re

_REF_RE = re.compile(r"\{RESULT_OF_STEP_(\d+)\}", re.IGNORECASE)
_PREV_RE = re.compile(r"\{RESULT_OF_PREVIOUS_STEP\}", re.IGNORECASE)


class ResultStore:
    def __init__(self) -> None:
        self._results: dict[int, str] = {}
        self._failed: set[int] = set()

    def store(self, step_num: int, result: str) -> None:
        self._results[step_num] = result

    def store_failure(self, step_num: int, message: str) -> None:
        """Store a failure placeholder. These are excluded from latest_success()."""
        self._results[step_num] = message
        self._failed.add(step_num)

    def get(self, step_num: int) -> str:
        return self._results.get(step_num, f"[Result of step {step_num} not available]")

    def latest(self) -> str:
        """Return the most recently stored result (including failure placeholders)."""
        if not self._results:
            return ""
        return self._results[max(self._results)]

    def latest_success(self) -> str:
        """Return the most recently stored result from a SUCCESSFUL step.

        Used by WRITEFILE and PROMPT auto-injection to avoid injecting failure
        placeholders into file content or analytical prompts.
        """
        success_keys = [k for k in self._results if k not in self._failed]
        if not success_keys:
            return ""
        return self._results[max(success_keys)]

    def substitute(self, text: str) -> str:
        """Replace {RESULT_OF_STEP_N} and {RESULT_OF_PREVIOUS_STEP} with stored values."""
        text = _REF_RE.sub(lambda m: self.get(int(m.group(1))), text)
        text = _PREV_RE.sub(self.latest(), text)
        return text
