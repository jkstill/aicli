"""Trace logger for diagnosing hangs and timing issues.

Usage:
    from .output.tracer import trace, init_tracer

    init_tracer("/tmp/aicli-trace.log")   # call once at startup
    trace("PLAN_START", "model=qwen3")    # call anywhere; flushes immediately
"""

import os
import time
from typing import TextIO


class Tracer:
    def __init__(self, path: str) -> None:
        self._start = time.monotonic()
        self._file: TextIO = open(path, "w", buffering=1)  # line-buffered
        self._write("TRACE_START", f"pid={os.getpid()} file={path}")

    def _write(self, event: str, message: str) -> None:
        elapsed = time.monotonic() - self._start
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._file.write(f"[{ts}] [+{elapsed:9.3f}s] {event:<30} {message}\n")
        self._file.flush()

    def trace(self, event: str, message: str = "") -> None:
        self._write(event, message)

    def close(self) -> None:
        self._write("TRACE_END", "")
        self._file.close()


# Module-level singleton — None when tracing is disabled.
_tracer: Tracer | None = None


def init_tracer(path: str | None) -> None:
    """Enable tracing. Call once at startup; pass None to disable."""
    global _tracer
    if path:
        _tracer = Tracer(path)


def trace(event: str, message: str = "") -> None:
    """Emit a trace line if tracing is enabled. No-op otherwise."""
    if _tracer is not None:
        _tracer.trace(event, message)


def close_tracer() -> None:
    global _tracer
    if _tracer is not None:
        _tracer.close()
        _tracer = None
