"""Tests for the V2 plan parser."""

import pytest
from aicli.core.plan_parser import parse_plan, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kw(steps, idx=0):
    return steps[idx].keyword

def _arg(steps, idx=0):
    return steps[idx].arg

def _body(steps, idx=0):
    return steps[idx].body


# ---------------------------------------------------------------------------
# Basic keyword parsing
# ---------------------------------------------------------------------------

def test_readfile_bare():
    steps = parse_plan("READFILE: cat /tmp/data.txt")
    assert len(steps) == 1
    assert _kw(steps) == "READFILE"
    assert _arg(steps) == "cat /tmp/data.txt"


def test_readfile_bare_path_normalized():
    """Bare absolute path should be converted to 'cat <path>'."""
    steps = parse_plan("READFILE: /tmp/data.txt")
    assert len(steps) == 1
    assert _arg(steps) == "cat /tmp/data.txt"


def test_readfile_file_key():
    """READFILE file=/path → cat /path."""
    steps = parse_plan('READFILE: file="/etc/hostname"')
    assert len(steps) == 1
    assert _arg(steps) == "cat /etc/hostname"


def test_readfile_path_key():
    steps = parse_plan("READFILE: path=/var/log/syslog")
    assert len(steps) == 1
    assert _arg(steps) == "cat /var/log/syslog"


def test_writefile_basic():
    plan = "WRITEFILE: /tmp/out.md\nHello world"
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert _kw(steps) == "WRITEFILE"
    assert _arg(steps) == "/tmp/out.md"
    assert "Hello world" in _body(steps)


def test_writefile_file_key():
    """WRITEFILE: file=/path content=... → path extracted."""
    plan = "WRITEFILE: file=/tmp/analysis.md content={RESULT_OF_STEP_1}"
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert _arg(steps) == "/tmp/analysis.md"


def test_listdir_basic():
    steps = parse_plan("LISTDIR: /home/user/data")
    assert len(steps) == 1
    assert _kw(steps) == "LISTDIR"
    assert _arg(steps) == "/home/user/data"


def test_listdir_path_key():
    steps = parse_plan("LISTDIR: path=/home/user/data")
    assert len(steps) == 1
    assert _arg(steps) == "/home/user/data"


def test_listdir_dir_key():
    steps = parse_plan("LISTDIR: dir=/tmp")
    assert len(steps) == 1
    assert _arg(steps) == "/tmp"


def test_exec_basic():
    steps = parse_plan("EXEC: gnuplot /tmp/chart.gp")
    assert len(steps) == 1
    assert _kw(steps) == "EXEC"
    assert _arg(steps) == "gnuplot /tmp/chart.gp"


def test_exec_command_key():
    steps = parse_plan('EXEC: command="python3 /tmp/run.py"')
    assert len(steps) == 1
    assert _arg(steps) == "python3 /tmp/run.py"


def test_prompt_basic():
    plan = "PROMPT: Summarize the data\nHere is the data: some stuff"
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert _kw(steps) == "PROMPT"
    assert "Summarize" in _arg(steps)


def test_gencode_with_saveas():
    plan = (
        "GENCODE: gnuplot\n"
        "SAVEAS: /tmp/chart.gp\n"
        "Plot columns 2 and 3 as a time series."
    )
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert _kw(steps) == "GENCODE"
    assert steps[0].save_path == "/tmp/chart.gp"
    assert "Plot columns" in steps[0].body


def test_gencode_output_key_in_arg():
    """output= in GENCODE arg line is used as save_path fallback."""
    plan = 'GENCODE: python output=/tmp/script.py\nWrite a script.'
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert steps[0].save_path == "/tmp/script.py"


def test_gencode_missing_saveas():
    plan = "GENCODE: bash\nDo something."
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert steps[0].save_path == ""


# ---------------------------------------------------------------------------
# Multi-step plans
# ---------------------------------------------------------------------------

def test_multi_step_plan():
    plan = (
        "READFILE: cat /tmp/data.csv\n"
        "PROMPT: Analyze this data\n"
        "{RESULT_OF_STEP_1}\n"
        "WRITEFILE: /tmp/report.md\n"
        "{RESULT_OF_STEP_2}\n"
    )
    steps = parse_plan(plan)
    assert len(steps) == 3
    assert steps[0].keyword == "READFILE"
    assert steps[1].keyword == "PROMPT"
    assert "{RESULT_OF_STEP_1}" in steps[1].body
    assert steps[2].keyword == "WRITEFILE"
    assert steps[0].number == 1
    assert steps[2].number == 3


# ---------------------------------------------------------------------------
# Format tolerance
# ---------------------------------------------------------------------------

def test_numbered_bullet_prefix():
    plan = (
        "1. READFILE: cat /tmp/a.txt\n"
        "2. WRITEFILE: /tmp/b.txt\nhello\n"
    )
    steps = parse_plan(plan)
    assert len(steps) == 2
    assert steps[0].keyword == "READFILE"
    assert steps[1].keyword == "WRITEFILE"


def test_step_n_colon_prefix():
    plan = (
        "Step 1: READFILE: cat /tmp/a.txt\n"
        "Step 2: PROMPT: Analyze it\n"
    )
    steps = parse_plan(plan)
    assert len(steps) == 2
    assert steps[0].keyword == "READFILE"
    assert steps[1].keyword == "PROMPT"


def test_lowercase_keywords():
    plan = "readfile: cat /tmp/a.txt\nwritefile: /tmp/b.txt\nhello\n"
    steps = parse_plan(plan)
    assert len(steps) == 2
    assert steps[0].keyword == "READFILE"
    assert steps[1].keyword == "WRITEFILE"


def test_outer_code_fence_stripped():
    plan = "```\nREADFILE: cat /tmp/a.txt\nWRITEFILE: /tmp/b.txt\nhello\n```"
    steps = parse_plan(plan)
    assert len(steps) == 2


def test_outer_fence_with_language_tag():
    plan = "```plaintext\nREADFILE: cat /tmp/a.txt\n```"
    steps = parse_plan(plan)
    assert len(steps) == 1
    assert steps[0].keyword == "READFILE"


def test_backtick_wrapped_step_lines():
    plan = "`READFILE: cat /tmp/a.txt`\n`PROMPT: Summarize it`\n"
    steps = parse_plan(plan)
    assert len(steps) == 2
    assert steps[0].keyword == "READFILE"
    assert steps[1].keyword == "PROMPT"


def test_empty_input():
    assert parse_plan("") == []


def test_no_keywords():
    assert parse_plan("This is just some prose without any step keywords.") == []


def test_space_separator_instead_of_colon():
    """Some models emit 'READFILE /path' instead of 'READFILE: /path'."""
    steps = parse_plan("READFILE /tmp/a.txt")
    assert len(steps) == 1
    assert steps[0].keyword == "READFILE"


def test_markdown_dash_bullet_prefix():
    """llama3.2-style '- KEYWORD arg' format."""
    plan = (
        "- READFILE /tmp/a.txt\n"
        "- PROMPT Analyze it\n"
        "- WRITEFILE /tmp/out.md\n"
        "result\n"
    )
    steps = parse_plan(plan)
    assert len(steps) == 3
    assert steps[0].keyword == "READFILE"
    assert steps[1].keyword == "PROMPT"
    assert steps[2].keyword == "WRITEFILE"


def test_markdown_star_bullet_prefix():
    plan = "* EXEC echo hello\n* WRITEFILE /tmp/out.md\nhello\n"
    steps = parse_plan(plan)
    assert len(steps) == 2
    assert steps[0].keyword == "EXEC"
    assert steps[1].keyword == "WRITEFILE"


def test_gencode_path_as_arg():
    """Models often emit 'GENCODE: /path/to/file.py' — path should become save_path."""
    steps = parse_plan("GENCODE: /tmp/script.py\nWrite a hello world script.")
    assert len(steps) == 1
    assert steps[0].save_path == "/tmp/script.py"
    assert steps[0].keyword == "GENCODE"


def test_gencode_path_as_arg_language_from_extension():
    """Language should be inferred from the file extension when path is used as arg."""
    steps = parse_plan("GENCODE: /tmp/chart.gp\nPlot data.")
    assert steps[0].save_path == "/tmp/chart.gp"
    assert steps[0].arg == "gp"


def test_gencode_path_as_arg_md_extension():
    steps = parse_plan("GENCODE: /tmp/report.md\nWrite a report.")
    assert steps[0].save_path == "/tmp/report.md"
    assert steps[0].arg == "md"


def test_gencode_path_as_arg_no_extension():
    steps = parse_plan("GENCODE: /tmp/Makefile\nWrite a Makefile.")
    assert steps[0].save_path == "/tmp/Makefile"
    assert steps[0].arg == "text"


# ---------------------------------------------------------------------------
# Step numbering
# ---------------------------------------------------------------------------

def test_step_numbers_are_sequential():
    plan = "READFILE: cat /a\nPROMPT: Analyze\nWRITEFILE: /b\nresult\n"
    steps = parse_plan(plan)
    assert [s.number for s in steps] == [1, 2, 3]


# ---------------------------------------------------------------------------
# WRITEFILE body handling
# ---------------------------------------------------------------------------

def test_writefile_multiline_body():
    plan = "WRITEFILE: /tmp/out.md\nLine 1\nLine 2\nLine 3\n"
    steps = parse_plan(plan)
    assert steps[0].body == "Line 1\nLine 2\nLine 3"


def test_writefile_result_ref_in_body():
    plan = "WRITEFILE: /tmp/out.md\n{RESULT_OF_STEP_2}\n"
    steps = parse_plan(plan)
    assert "{RESULT_OF_STEP_2}" in steps[0].body
