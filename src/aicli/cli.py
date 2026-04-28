"""aicli v2 — planner/executor CLI for large language models."""

import signal
import sys
from pathlib import Path

import click

from .config import driver_config, filter_models, load_config, resolve_api_key
from .core.orchestrator import Orchestrator
from .core.plan_parser import parse_plan
from .core.planner import Planner, load_system_prompt
from .drivers.registry import get_driver, list_drivers
from .output.logger import SessionLogger
from .output.renderer import Renderer


def _parse_model(model_str: str) -> tuple[str, str]:
    """Split 'driver/model-name' into (driver, model). Defaults to ollama."""
    if "/" in model_str:
        prefix, _, rest = model_str.partition("/")
        if prefix.lower() in list_drivers():
            return prefix.lower(), rest
        return "ollama", model_str
    return "ollama", model_str


def _setup_driver(driver_name: str, model_name: str, config: dict, api_base=None, api_key=None):
    driver = get_driver(driver_name)
    dcfg = driver_config(config, driver_name)
    effective_base = api_base or dcfg.get("api_base", "")
    effective_key = api_key or resolve_api_key(dcfg)
    driver.configure(effective_base, effective_key, model_name)
    return driver


def run_task(
    task: str,
    planner_driver,
    analysis_driver,
    allowed_dirs: list[str],
    allow_exec: bool,
    auto_approve: bool,
    dry_run: bool,
    verbose: bool,
    system_prompt: str,
    renderer: Renderer,
    logger: SessionLogger,
    exec_timeout: int = 300,
) -> None:
    """Run one task: plan → parse → execute."""
    logger.log("user", task)

    # --- Phase 1: Get the plan from the planner ---
    renderer.print_info("Planning...")
    planner = Planner(planner_driver, system_prompt)

    plan_text = ""
    if verbose:
        def on_plan_chunk(t: str) -> None:
            nonlocal plan_text
            plan_text += t
            renderer.stream_chunk(t)
        plan_text = planner.get_plan(task, stream_callback=on_plan_chunk)
        renderer.finalize()
    else:
        plan_text = planner.get_plan(task)

    logger.log("assistant", plan_text)

    # --- Phase 2: Parse the plan ---
    steps = parse_plan(plan_text)

    if not steps:
        renderer.print_warning("No plan steps found in model response.")
        if not verbose:
            renderer.print_info("Raw response:")
            print(plan_text)
        return

    renderer.print_plan(steps)

    # --- Phase 3: Execute the plan ---
    if dry_run:
        renderer.print_info("[dry-run] Plan shown above. No steps will be executed.")
        return

    orchestrator = Orchestrator(
        analysis_driver=analysis_driver,
        allowed_dirs=allowed_dirs,
        allow_exec=allow_exec,
        auto_approve=auto_approve,
        dry_run=False,
        verbose=verbose,
        renderer=renderer,
        exec_timeout=exec_timeout,
    )

    final_output = orchestrator.run(steps)
    logger.log("tool", f"[orchestrator completed {len(steps)} steps]")

    if final_output:
        logger.log("assistant", final_output)


@click.command()
@click.option("--model", "-m", default=None,
              help="Planner model as driver/model-name (e.g. ollama/qwen3.5)")
@click.option("--analysis-model", "analysis_model", default=None,
              help="Analysis model for PROMPT/GENCODE steps (default: same as --model)")
@click.option("--prompt-file", "-f", "prompt_file", type=click.Path(exists=True), default=None,
              help="Read task prompt from file")
@click.option("--system-prompt-file", "system_prompt_file", type=click.Path(exists=True), default=None,
              help="Override the built-in planner system prompt")
@click.option("--include-directories", "-d", "include_directories", default=None,
              help="Comma-separated directories the framework may write to")
@click.option("--allow-exec", "allow_exec", is_flag=True, default=False,
              help="Permit shell command execution (EXEC steps)")
@click.option("--auto-approve", "-y", "auto_approve", is_flag=True, default=False,
              help="Skip confirmation prompts for writes and execution")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Show the parsed plan without executing any steps")
@click.option("--verbose", is_flag=True, default=False,
              help="Show substituted prompts and full step details")
@click.option("--no-markdown", "no_markdown", is_flag=True, default=False,
              help="Disable Markdown rendering")
@click.option("--list-models", "list_models", is_flag=True, default=False,
              help="List available models for the driver and exit")
@click.option("--api-base", "api_base", default=None, help="Override driver API base URL")
@click.option("--api-key", "api_key", default=None, help="Override driver API key")
@click.option("--log-sessions", "log_sessions", is_flag=True, default=False,
              help="Log session to file")
def main(
    model,
    analysis_model,
    prompt_file,
    system_prompt_file,
    include_directories,
    allow_exec,
    auto_approve,
    dry_run,
    verbose,
    no_markdown,
    list_models,
    api_base,
    api_key,
    log_sessions,
):
    """aicli v2 — planner/executor CLI for large language models.

    The LLM decomposes your task into discrete steps. The framework executes
    mechanical steps (file I/O, shell commands) and dispatches analytical steps
    (PROMPT, GENCODE) back to the LLM.
    """
    config = load_config()

    # --- Resolve planner model ---
    model = model or config.get("model", "ollama/qwen3.5")
    planner_name, planner_model = _parse_model(model)
    planner_driver = _setup_driver(planner_name, planner_model, config, api_base, api_key)

    if list_models:
        for name in filter_models(planner_driver.list_models(), config):
            click.echo(name)
        return

    # --- Resolve analysis model (defaults to planner) ---
    if analysis_model:
        analysis_name, analysis_model_name = _parse_model(analysis_model)
        analysis_driver = _setup_driver(analysis_name, analysis_model_name, config, api_base, api_key)
    else:
        analysis_driver = planner_driver
        analysis_name = planner_name
        analysis_model_name = planner_model

    # --- Load system prompt ---
    system_prompt = load_system_prompt(system_prompt_file)

    # --- Renderer and logger ---
    use_markdown = not no_markdown and config.get("output_format", "markdown") == "markdown"
    renderer = Renderer(markdown=use_markdown)
    log_dir = config.get("log_dir", "~/.local/share/aicli/logs")
    logger = SessionLogger(
        log_dir=log_dir,
        enabled=log_sessions or config.get("log_sessions", False),
    )

    # --- Allowed directories ---
    allowed_dirs = [d.strip() for d in include_directories.split(",")] if include_directories else []

    exec_timeout = config.get("exec_timeout", 300)

    renderer.print_info(
        f"aicli v2 | planner={planner_name}/{planner_model} "
        f"analysis={analysis_name}/{analysis_model_name} "
        f"exec={allow_exec} dirs={allowed_dirs if allowed_dirs else 'none'}"
    )

    # --- Pipe mode ---
    if not sys.stdin.isatty() and not prompt_file:
        task = sys.stdin.read().strip()
        if task:
            run_task(
                task, planner_driver, analysis_driver,
                allowed_dirs, allow_exec, auto_approve, dry_run, verbose,
                system_prompt, renderer, logger, exec_timeout,
            )
        logger.close()
        return

    # --- File mode ---
    if prompt_file:
        task = Path(prompt_file).read_text().strip()
        run_task(
            task, planner_driver, analysis_driver,
            allowed_dirs, allow_exec, auto_approve, dry_run, verbose,
            system_prompt, renderer, logger, exec_timeout,
        )
        logger.close()
        return

    # --- Interactive REPL ---
    renderer.print_info("Interactive mode — type 'exit' or Ctrl-D to quit.")

    def _sigint(sig, frame):
        print()
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)

    while True:
        try:
            task = input("\naicli> ").strip()
        except EOFError:
            print()
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit", "q"):
            break

        run_task(
            task, planner_driver, analysis_driver,
            allowed_dirs, allow_exec, auto_approve, dry_run, verbose,
            system_prompt, renderer, logger, exec_timeout,
        )

    logger.close()


if __name__ == "__main__":
    main()
