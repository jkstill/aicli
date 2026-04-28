"""Orchestrator: executes a parsed plan step by step.

Mechanical steps (READFILE, WRITEFILE, LISTDIR, EXEC) are executed by the
framework directly. Analytical steps (PROMPT, GENCODE) are dispatched to
the analysis LLM driver. Results flow between steps via the ResultStore.
"""

import re
import subprocess
from typing import TYPE_CHECKING

from .actions import ActionRequest, ActionType, WriteMode
from .executor import Executor
from .executor import PermissionError as ExecPermissionError
from .plan_parser import PlanStep
from .result_store import ResultStore
from ..output.tracer import trace

if TYPE_CHECKING:
    from ..output.renderer import Renderer


_FENCE_RE = re.compile(r"```[a-z]*\n(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def _strip_code_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1)
    return text.strip()


class StepResult:
    def __init__(self, success: bool, output: str = "", error: str = "") -> None:
        self.success = success
        self.output = output
        self.error = error


class Orchestrator:
    def __init__(
        self,
        analysis_driver,
        allowed_dirs: list[str],
        allow_exec: bool = False,
        auto_approve: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
        renderer: "Renderer | None" = None,
        exec_timeout: int = 300,
        on_error: str = "ask",
    ) -> None:
        self._analysis = analysis_driver
        self._executor = Executor(allowed_dirs=allowed_dirs, allow_exec=allow_exec)
        self._auto_approve = auto_approve
        self._dry_run = dry_run
        self._verbose = verbose
        self._renderer = renderer
        self._exec_timeout = exec_timeout
        self._on_error = on_error  # "continue" | "abort" | "ask"

    # ------------------------------------------------------------------
    # Renderer helpers
    # ------------------------------------------------------------------

    def _info(self, text: str) -> None:
        if self._renderer:
            self._renderer.print_info(text)

    def _warn(self, text: str) -> None:
        if self._renderer:
            self._renderer.print_warning(text)

    def _error(self, text: str) -> None:
        if self._renderer:
            self._renderer.print_error(text)

    def _confirm(self, prompt: str) -> bool:
        if self._auto_approve:
            return True
        if self._renderer:
            return self._renderer.confirm(prompt)
        return True

    # ------------------------------------------------------------------
    # Driver helper
    # ------------------------------------------------------------------

    def _collect_text(
        self,
        messages: list[dict],
        system_prompt: str = "",
        stream_callback=None,
    ) -> str:
        """Send messages to the analysis driver and collect the full response."""
        text = ""
        for chunk in self._analysis.send(
            messages,
            system_prompt=system_prompt,
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

    # ------------------------------------------------------------------
    # Step executors
    # ------------------------------------------------------------------

    def _exec_readfile(self, step: PlanStep, store: ResultStore) -> StepResult:
        command = store.substitute(step.arg)
        if self._verbose:
            self._info(f"    command: {command}")
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            output = proc.stdout
            if proc.stderr:
                output += "\n" + proc.stderr
            return StepResult(success=True, output=output)
        except subprocess.TimeoutExpired:
            return StepResult(success=False, error="Command timed out after 30s.")
        except OSError as e:
            return StepResult(success=False, error=str(e))

    def _exec_writefile(self, step: PlanStep, store: ResultStore) -> StepResult:
        path = store.substitute(step.arg)
        body = step.body
        # Auto-inject the latest SUCCESSFUL step result if the body is empty.
        # latest_success() skips failure placeholders so a preceding GENCODE
        # failure doesn't overwrite the file with "[Step N failed: ...]".
        if not body.strip():
            body = store.latest_success()
        content = store.substitute(body)
        req = ActionRequest(
            action_type=ActionType.WRITE_FILE,
            params={"path": path, "content": content, "mode": WriteMode.OVERWRITE},
        )
        try:
            result = self._executor.execute(req)
            if result.success:
                return StepResult(success=True, output=f"Written to {path}")
            return StepResult(success=False, error=result.error or "Write failed.")
        except ExecPermissionError as e:
            return StepResult(success=False, error=str(e))

    def _exec_listdir(self, step: PlanStep, store: ResultStore) -> StepResult:
        parts = store.substitute(step.arg).split()
        path = parts[0] if parts else "."
        recursive = "--recursive" in parts or "-r" in parts
        req = ActionRequest(
            action_type=ActionType.LIST_DIRECTORY,
            params={"path": path, "recursive": recursive},
        )
        result = self._executor.execute(req)
        if result.success:
            entries = result.data.get("entries", [])
            return StepResult(success=True, output="\n".join(entries))
        return StepResult(success=False, error=result.error or "")

    def _exec_exec(self, step: PlanStep, store: ResultStore) -> StepResult:
        command = store.substitute(step.arg)
        if self._verbose:
            self._info(f"    command: {command}")
        req = ActionRequest(
            action_type=ActionType.EXECUTE,
            params={"command": command, "timeout": self._exec_timeout},
        )
        result = self._executor.execute(req)
        if result.success:
            stdout = result.data.get("stdout", "")
            stderr = result.data.get("stderr", "")
            exit_code = result.data.get("exit_code", 0)
            combined = stdout
            if stderr:
                combined += f"\n[stderr]: {stderr}"
            if exit_code == 0:
                return StepResult(success=True, output=combined)
            err = stderr.strip() or stdout.strip() or f"exit code {exit_code}"
            return StepResult(success=False, error=f"Command failed ({err})", output=combined)
        return StepResult(success=False, error=result.error or "Execution failed.")

    def _exec_prompt(self, step: PlanStep, store: ResultStore) -> StepResult:
        raw = step.arg
        if step.body:
            raw = (raw + "\n" + step.body) if raw else step.body

        # Auto-inject the most recent SUCCESSFUL step result if no explicit ref.
        # Uses latest_success() so a preceding failure placeholder is not injected.
        ref_pattern = re.compile(r"\{RESULT_OF_", re.IGNORECASE)
        if not ref_pattern.search(raw):
            prev = store.latest_success()
            if prev:
                raw = raw + "\n\nData:\n" + prev

        prompt_text = store.substitute(raw)

        if self._verbose:
            preview = prompt_text[:120].replace("\n", " ")
            self._info(f"    prompt: {preview}...")

        text = ""
        first_token = True

        def on_chunk(t: str) -> None:
            nonlocal text, first_token
            if first_token:
                trace("STEP_FIRST_TOKEN", f"step={step.number} keyword=PROMPT")
                first_token = False
            text += t
            if self._renderer:
                self._renderer.stream_chunk(t)

        trace("STEP_LLM_REQUEST", f"step={step.number} keyword=PROMPT prompt_len={len(prompt_text)}")
        self._collect_text(
            [{"role": "user", "content": prompt_text}],
            stream_callback=on_chunk,
        )
        if self._renderer:
            self._renderer.finalize()
        trace("STEP_LLM_DONE", f"step={step.number} keyword=PROMPT response_len={len(text)}")

        return StepResult(success=True, output=text)

    def _exec_gencode(self, step: PlanStep, store: ResultStore) -> StepResult:
        if not step.save_path:
            return StepResult(success=False, error="GENCODE step is missing a SAVEAS: line.")

        language = step.arg
        instructions = store.substitute(step.body)
        save_path = store.substitute(step.save_path)

        if self._verbose:
            self._info(f"    language={language} save={save_path}")

        system = (
            f"Generate only {language} code. "
            "Output raw code only. No explanatory text. No markdown code fences."
        )
        text = ""
        first_token = True

        def on_chunk(t: str) -> None:
            nonlocal text, first_token
            if first_token:
                trace("STEP_FIRST_TOKEN", f"step={step.number} keyword=GENCODE")
                first_token = False
            text += t
            if self._renderer:
                self._renderer.stream_chunk(t)

        trace("STEP_LLM_REQUEST", f"step={step.number} keyword=GENCODE lang={language} save={save_path}")
        raw = self._collect_text(
            [{"role": "user", "content": instructions}],
            system_prompt=system,
            stream_callback=on_chunk,
        )
        if self._renderer:
            self._renderer.finalize()
        trace("STEP_LLM_DONE", f"step={step.number} keyword=GENCODE response_len={len(raw)}")

        code = _strip_code_fences(raw)
        req = ActionRequest(
            action_type=ActionType.WRITE_FILE,
            params={"path": save_path, "content": code, "mode": WriteMode.OVERWRITE},
        )
        try:
            result = self._executor.execute(req)
            if result.success:
                return StepResult(success=True, output=code)
            return StepResult(success=False, error=result.error or "Failed to write generated code.")
        except ExecPermissionError as e:
            return StepResult(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, steps: list[PlanStep]) -> str:
        """Execute a list of parsed plan steps.

        Returns the text output of the last PROMPT step (if any), suitable for
        display to the user.
        """
        store = ResultStore()
        final_output = ""
        total = len(steps)

        # Warn early if the plan contains EXEC steps but --allow-exec is not set.
        exec_steps = [s for s in steps if s.keyword == "EXEC"]
        if exec_steps and not self._executor.allow_exec:
            self._warn(
                f"Plan contains {len(exec_steps)} EXEC step(s) but --allow-exec is not set. "
                "Those steps will fail. Re-run with --allow-exec to enable execution."
            )

        trace("PLAN_EXEC_START", f"total_steps={total}")
        for step in steps:
            self._info(f"\n[{step.number}/{total}] {step.keyword}: {step.arg[:70]}")
            trace("STEP_START", f"step={step.number}/{total} keyword={step.keyword} arg={step.arg[:60]!r}")

            if self._dry_run:
                self._info("  [dry-run] skipped")
                store.store(step.number, f"[dry-run placeholder for step {step.number}]")
                trace("STEP_SKIPPED", f"step={step.number} reason=dry_run")
                continue

            needs_confirm = step.keyword in ("WRITEFILE", "EXEC", "GENCODE")
            if needs_confirm and not self._auto_approve:
                label = f"{step.arg[:60]}" if step.arg else step.keyword
                if not self._confirm(f"  Allow {step.keyword} ({label})?"):
                    self._info("  Skipped by user.")
                    store.store(step.number, "[skipped by user]")
                    trace("STEP_SKIPPED", f"step={step.number} reason=user_denied")
                    continue

            result: StepResult
            match step.keyword:
                case "READFILE":
                    result = self._exec_readfile(step, store)
                case "WRITEFILE":
                    result = self._exec_writefile(step, store)
                case "LISTDIR":
                    result = self._exec_listdir(step, store)
                case "EXEC":
                    result = self._exec_exec(step, store)
                case "PROMPT":
                    result = self._exec_prompt(step, store)
                case "GENCODE":
                    result = self._exec_gencode(step, store)
                case _:
                    result = StepResult(
                        success=False,
                        error=f"Unknown step keyword: {step.keyword}",
                    )

            if result.success:
                self._info("  ✓ done")
                store.store(step.number, result.output)
                if step.keyword == "PROMPT":
                    final_output = result.output
                trace("STEP_DONE", f"step={step.number} keyword={step.keyword} success=True output_len={len(result.output)}")
            else:
                self._error(f"  ✗ {result.error}")
                store.store_failure(step.number, f"[Step {step.number} failed: {result.error}]")
                trace("STEP_DONE", f"step={step.number} keyword={step.keyword} success=False error={result.error!r}")
                if self._on_error == "abort":
                    self._error("Aborting plan execution after step failure.")
                    return final_output
                elif self._on_error == "ask" and not self._auto_approve:
                    if not self._confirm("  Step failed. Continue with remaining steps?"):
                        self._info("Execution aborted by user.")
                        return final_output

        return final_output
