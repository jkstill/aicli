"""Session logging to file."""

import datetime
from pathlib import Path


class SessionLogger:
    def __init__(self, log_dir: str | None = None, enabled: bool = False):
        self.enabled = enabled
        self._file = None
        if enabled and log_dir:
            log_path = Path(log_dir).expanduser()
            log_path.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self._file = open(log_path / f"session_{ts}.log", "w")

    def log(self, role: str, content: str) -> None:
        if self._file:
            self._file.write(f"\n[{role.upper()}]\n{content}\n")
            self._file.flush()

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def __del__(self):
        self.close()
