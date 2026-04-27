"""Tests for the action block parser."""

import pytest
from aicli.core.parser import (
    parse_action_blocks,
    parse_function_call_blocks,
    split_text_and_actions,
)
from aicli.core.actions import ActionType, WriteMode


def test_parse_read_file():
    text = '''
<aicli_action type="read_file">
path: /tmp/test.txt
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 1
    a = actions[0]
    assert a.action_type == ActionType.READ_FILE
    assert a.get("path") == "/tmp/test.txt"


def test_parse_write_file_heredoc():
    text = '''
<aicli_action type="write_file">
path: /tmp/out.txt
mode: create
content:
<<<CONTENT
Hello, world!
Line 2
CONTENT>>>
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 1
    a = actions[0]
    assert a.action_type == ActionType.WRITE_FILE
    assert a.get("path") == "/tmp/out.txt"
    assert a.get("mode") == WriteMode.CREATE
    assert "Hello, world!" in a.get("content", "")
    assert "Line 2" in a.get("content", "")


def test_parse_execute():
    text = '''
<aicli_action type="execute">
command: echo hello
working_dir: /tmp
timeout: 10
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 1
    a = actions[0]
    assert a.action_type == ActionType.EXECUTE
    assert a.get("command") == "echo hello"
    assert a.get("working_dir") == "/tmp"
    assert a.get("timeout") == 10


def test_parse_list_directory():
    text = '''
<aicli_action type="list_directory">
path: /tmp
recursive: true
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 1
    assert actions[0].action_type == ActionType.LIST_DIRECTORY
    assert actions[0].get("recursive") is True


def test_parse_multiple_actions():
    text = '''
Some preamble text.
<aicli_action type="read_file">
path: /tmp/a.txt
</aicli_action>
Some middle text.
<aicli_action type="execute">
command: ls /tmp
</aicli_action>
Trailing text.
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 2
    assert actions[0].action_type == ActionType.READ_FILE
    assert actions[1].action_type == ActionType.EXECUTE


def test_split_removes_action_blocks():
    text = '''
Here is my response.
<aicli_action type="read_file">
path: /tmp/test.txt
</aicli_action>
Continuing after the action.
'''
    clean, actions = split_text_and_actions(text)
    assert len(actions) == 1
    assert "<aicli_action" not in clean
    assert "Here is my response." in clean
    assert "Continuing after the action." in clean


def test_unknown_action_type_skipped():
    text = '''
<aicli_action type="fly_to_moon">
destination: Luna
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 0


def test_missing_required_param_skipped():
    # write_file without path should be skipped
    text = '''
<aicli_action type="write_file">
content:
<<<CONTENT
data
CONTENT>>>
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 0


def test_search_files():
    text = '''
<aicli_action type="search_files">
pattern: *.py
path: /tmp
type: glob
</aicli_action>
'''
    actions = list(parse_action_blocks(text))
    assert len(actions) == 1
    a = actions[0]
    assert a.action_type == ActionType.SEARCH_FILES
    assert a.get("pattern") == "*.py"
    assert a.get("type") == "glob"


# --- <function=...> format (qwen3-coder and similar models) ---

_FUNC_WRITE = '''\
I will write the file now.
<function=write_file>
<parameter=filepath>
/tmp/hello.py
</parameter>
<parameter=content>
print("hello")
</parameter>
</function>
</tool_call>
'''

_FUNC_READ = '''\
<function=read_file>
<parameter=filepath>
/tmp/hello.py
</parameter>
</function>
'''

_FUNC_EXECUTE = '''\
<function=execute>
<parameter=cmd>echo hello</parameter>
</function>
'''


def test_parse_function_call_write():
    actions = list(parse_function_call_blocks(_FUNC_WRITE))
    assert len(actions) == 1
    a = actions[0]
    assert a.action_type == ActionType.WRITE_FILE
    assert a.get("path") == "/tmp/hello.py"
    assert 'print("hello")' in a.get("content", "")
    assert a.get("mode") == WriteMode.OVERWRITE


def test_parse_function_call_read():
    actions = list(parse_function_call_blocks(_FUNC_READ))
    assert len(actions) == 1
    assert actions[0].action_type == ActionType.READ_FILE
    assert actions[0].get("path") == "/tmp/hello.py"


def test_parse_function_call_execute_alias():
    actions = list(parse_function_call_blocks(_FUNC_EXECUTE))
    assert len(actions) == 1
    assert actions[0].action_type == ActionType.EXECUTE
    assert actions[0].get("command") == "echo hello"


def test_split_removes_function_call_blocks():
    clean, actions = split_text_and_actions(_FUNC_WRITE)
    assert len(actions) == 1
    assert "<function=" not in clean
    assert "</tool_call>" not in clean
    assert "I will write the file now." in clean


def test_split_removes_tool_call_tags():
    text = "preamble\n</tool_call>\npostamble"
    clean, actions = split_text_and_actions(text)
    assert "</tool_call>" not in clean


def test_parse_function_call_unknown_skipped():
    text = "<function=fly_to_moon><parameter=dest>Luna</parameter></function>"
    actions = list(parse_function_call_blocks(text))
    assert len(actions) == 0


def test_parse_function_call_missing_required_skipped():
    # write_file without content → skip
    text = "<function=write_file><parameter=filepath>/tmp/x.txt</parameter></function>"
    actions = list(parse_function_call_blocks(text))
    assert len(actions) == 0
