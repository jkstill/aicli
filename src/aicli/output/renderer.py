"""Streaming terminal output with optional Markdown rendering via rich."""

import sys

try:
    from rich.console import Console
    from rich.markdown import Markdown
    _RICH = True
except ImportError:
    _RICH = False


class Renderer:
    def __init__(self, markdown: bool = True, use_rich: bool = True):
        self.markdown = markdown and _RICH and use_rich
        if self.markdown:
            self._console = Console(highlight=False)
        self._buffer = ""

    def stream_chunk(self, text: str) -> None:
        """Write a raw text chunk to stdout while streaming."""
        self._buffer += text
        sys.stdout.write(text)
        sys.stdout.flush()

    def finalize(self) -> None:
        """Called when streaming is complete. Re-render with Markdown if enabled."""
        if self.markdown and self._buffer.strip():
            # Clear what we streamed and re-render nicely.
            # (Rich doesn't support live partial Markdown; we render at end.)
            print()  # newline after raw stream
            self._console.print(Markdown(self._buffer))
        else:
            print()  # just end the line
        self._buffer = ""

    def print_info(self, text: str) -> None:
        if self.markdown:
            self._console.print(f"[dim]{text}[/dim]")
        else:
            print(text, file=sys.stderr)

    def print_warning(self, text: str) -> None:
        if self.markdown:
            self._console.print(f"[yellow]{text}[/yellow]")
        else:
            print(f"WARNING: {text}", file=sys.stderr)

    def print_error(self, text: str) -> None:
        if self.markdown:
            self._console.print(f"[red bold]{text}[/red bold]")
        else:
            print(f"ERROR: {text}", file=sys.stderr)

    def print_action_header(self, action_type: str, summary: str) -> None:
        if self.markdown:
            self._console.print(f"\n[cyan bold]Action:[/cyan bold] [cyan]{action_type}[/cyan] — {summary}")
        else:
            print(f"\n[Action: {action_type}] {summary}")

    def print_action_result(self, success: bool, message: str) -> None:
        if success:
            if self.markdown:
                self._console.print(f"[green]  ✓ {message}[/green]")
            else:
                print(f"  OK: {message}")
        else:
            if self.markdown:
                self._console.print(f"[red]  ✗ {message}[/red]")
            else:
                print(f"  FAIL: {message}")

    def confirm(self, prompt: str) -> bool:
        """Ask user Y/n confirmation. Returns True for yes."""
        try:
            answer = input(f"{prompt} [Y/n] ").strip().lower()
            return answer in ("", "y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    # ------------------------------------------------------------------
    # V2 planner/executor display
    # ------------------------------------------------------------------

    def print_plan(self, steps: list) -> None:
        """Display the parsed plan before execution."""
        header = f"Plan: {len(steps)} step(s)"
        if self.markdown:
            self._console.print(f"\n[bold cyan]{header}[/bold cyan]")
            for step in steps:
                save = f" → {step.save_path}" if step.save_path else ""
                self._console.print(
                    f"  [dim]{step.number:2}.[/dim] "
                    f"[cyan]{step.keyword}[/cyan]: {step.arg[:70]}{save}"
                )
        else:
            print(f"\n{header}")
            for step in steps:
                save = f" -> {step.save_path}" if step.save_path else ""
                print(f"  {step.number:2}. {step.keyword}: {step.arg[:70]}{save}")
