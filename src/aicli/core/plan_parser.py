"""Parser for V2 planner-mode step lists.

Parses LLM output containing lines of the form:
  KEYWORD: argument
  ...continuation body...

into PlanStep objects. Tolerates common model formatting variations:
optional "Step N:" prefixes, numbered list bullets, lowercase keywords,
and outer code fences wrapping the entire plan.
"""

import re
from dataclasses import dataclass

KEYWORDS = frozenset(["READFILE", "WRITEFILE", "LISTDIR", "EXEC", "PROMPT", "GENCODE"])

# Match: optional "Step N:" / "N." / "N)" bullet / "- " list marker, then KEYWORD: rest
_STEP_RE = re.compile(
    r"^\s*"
    r"(?:step\s*\d+\s*[.:)]\s*)?"   # optional "Step N:" prefix
    r"(?:\d+\s*[.)]\s*)?"            # optional "1." or "1)" bullet
    r"(?:[-*]\s+)?"                  # optional "- " or "* " markdown list marker
    r"(" + "|".join(KEYWORDS) + r")"
    r"\s*[: ]\s*(.*)",
    re.IGNORECASE,
)

# SAVEAS (and common aliases) within a GENCODE block
_SAVEAS_RE = re.compile(
    r"^\s*(?:SAVEAS|SAVE_AS|SAVE\s+AS|OUTPUT|SAVE\s+TO|SAVE)\s*:\s*(.+)",
    re.IGNORECASE,
)


@dataclass
class PlanStep:
    number: int     # 1-indexed step number
    keyword: str    # uppercase: READFILE, WRITEFILE, LISTDIR, EXEC, PROMPT, GENCODE
    arg: str        # text on the keyword line after the colon
    body: str       # multi-line body following the keyword line
    save_path: str = ""  # extracted SAVEAS path (GENCODE steps only)


def _strip_outer_fence(text: str) -> str:
    """Remove a wrapping ``` ... ``` code fence if the whole response is fenced."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    end = len(lines) - 1
    while end > 0 and not lines[end].strip().startswith("```"):
        end -= 1
    if end > 0:
        return "\n".join(lines[1:end])
    return text


_KV_RE = re.compile(r'(\w+)\s*=\s*["\']?([^"\']+?)["\']?\s*(?=$|\s\w+=)', re.DOTALL)
_PATH_RE = re.compile(
    r'^(?:path|file|filename|filepath|dir|directory)\s*=\s*["\']?([^"\']+)["\']?',
    re.IGNORECASE,
)
_CMD_RE = re.compile(r'^command\s*=\s*["\']?(.+?)["\']?\s*$', re.IGNORECASE)
_OUT_RE = re.compile(r'output\s*=\s*["\']?([^"\']+)["\']?', re.IGNORECASE)
_WRITEFILE_PATH_RE = re.compile(
    r'^(?:file|path|filename|filepath|destination|dest)\s*=\s*["\']?([^\s"\']+)["\']?',
    re.IGNORECASE,
)


def _normalize_readfile_arg(arg: str) -> str:
    """Convert bare paths or path=... key-value to a shell read command."""
    m = _PATH_RE.match(arg)
    if m:
        return f"cat {m.group(1).strip()}"
    a = arg.strip()
    if (a.startswith("/") or a.startswith("~")) and " " not in a:
        return f"cat {a}"
    return arg


def _normalize_exec_arg(arg: str) -> str:
    """Unwrap command=... key-value to the bare command string."""
    m = _CMD_RE.match(arg.strip())
    if m:
        return m.group(1).strip()
    return arg


def _normalize_listdir_arg(arg: str) -> str:
    """Unwrap path=... key-value to a bare path (preserving any --recursive flag)."""
    m = _PATH_RE.match(arg.strip())
    if m:
        return m.group(1).strip()
    return arg


def _extract_saveas(body_lines: list[str]) -> tuple[str, list[str]]:
    """Extract the first SAVEAS line from body_lines.

    Returns (save_path, remaining_lines) where save_path may be empty.
    """
    save_path = ""
    remaining: list[str] = []
    for line in body_lines:
        m = _SAVEAS_RE.match(line)
        if m and not save_path:
            save_path = m.group(1).strip()
        else:
            remaining.append(line)
    return save_path, remaining


def _make_step(number: int, keyword: str, arg: str, body_lines: list[str]) -> PlanStep:
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()

    body = "\n".join(body_lines)
    save_path = ""

    match keyword:
        case "READFILE":
            arg = _normalize_readfile_arg(arg)

        case "EXEC":
            arg = _normalize_exec_arg(arg)

        case "LISTDIR":
            arg = _normalize_listdir_arg(arg)

        case "WRITEFILE":
            m = _WRITEFILE_PATH_RE.match(arg.strip())
            if m:
                arg = m.group(1).strip()

        case "GENCODE":
            # Try SAVEAS from body first
            save_path, kept = _extract_saveas(body_lines)
            body = "\n".join(kept).rstrip("\n")
            # Fallback: look for output=... in the keyword arg line
            if not save_path:
                m = _OUT_RE.search(arg)
                if m:
                    save_path = m.group(1).strip()
            # Fallback: if arg itself looks like a file path, treat it as save_path.
            # Models commonly emit "GENCODE: /path/to/file.md" instead of
            # "GENCODE: markdown\nSAVEAS: /path/to/file.md".
            if not save_path:
                a = arg.strip()
                if a.startswith("/") or a.startswith("~"):
                    save_path = a
                    # Infer language from file extension (e.g. ".py" → "python" extension,
                    # ".md" → "markdown"); fall back to "text".
                    ext = a.rsplit(".", 1)[-1] if "." in a else ""
                    arg = ext if (ext and len(ext) <= 12) else "text"

    return PlanStep(number=number, keyword=keyword, arg=arg, body=body, save_path=save_path)


def parse_plan(text: str) -> list[PlanStep]:
    """Parse an LLM plan response into an ordered list of PlanStep objects."""
    text = _strip_outer_fence(text)

    steps: list[PlanStep] = []
    current_kw: str | None = None
    current_arg = ""
    current_body: list[str] = []
    step_num = 0

    for raw_line in text.splitlines():
        # Strip inline backtick wrappers that some models add around step lines.
        line = raw_line.strip("`").strip()
        m = _STEP_RE.match(line)
        if m:
            if current_kw is not None:
                step_num += 1
                steps.append(_make_step(step_num, current_kw, current_arg, current_body))
            current_kw = m.group(1).upper()
            current_arg = m.group(2).strip()
            current_body = []
        elif current_kw is not None:
            current_body.append(line)

    if current_kw is not None:
        step_num += 1
        steps.append(_make_step(step_num, current_kw, current_arg, current_body))

    return steps
