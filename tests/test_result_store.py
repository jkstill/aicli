"""Tests for the V2 result store."""

import pytest
from aicli.core.result_store import ResultStore


def test_store_and_get():
    rs = ResultStore()
    rs.store(1, "hello")
    assert rs.get(1) == "hello"


def test_get_missing_returns_placeholder():
    rs = ResultStore()
    result = rs.get(99)
    assert "99" in result
    assert "not available" in result.lower()


def test_latest_empty():
    rs = ResultStore()
    assert rs.latest() == ""


def test_latest_returns_last_stored():
    rs = ResultStore()
    rs.store(1, "first")
    rs.store(2, "second")
    rs.store(3, "third")
    assert rs.latest() == "third"


def test_latest_tracks_highest_step_number():
    rs = ResultStore()
    rs.store(3, "third")
    rs.store(1, "first")
    rs.store(2, "second")
    # latest() returns the value with the highest step number, not insertion order
    assert rs.latest() == "third"


def test_substitute_result_of_step_n():
    rs = ResultStore()
    rs.store(1, "the data")
    text = "Here is {RESULT_OF_STEP_1} for you."
    assert rs.substitute(text) == "Here is the data for you."


def test_substitute_result_of_previous_step():
    rs = ResultStore()
    rs.store(1, "step one output")
    text = "Analyze: {RESULT_OF_PREVIOUS_STEP}"
    assert rs.substitute(text) == "Analyze: step one output"


def test_substitute_multiple_refs():
    rs = ResultStore()
    rs.store(1, "A")
    rs.store(2, "B")
    text = "{RESULT_OF_STEP_1} and {RESULT_OF_STEP_2}"
    assert rs.substitute(text) == "A and B"


def test_substitute_case_insensitive():
    rs = ResultStore()
    rs.store(1, "val")
    text = "{result_of_step_1}"
    assert rs.substitute(text) == "val"


def test_substitute_no_refs():
    rs = ResultStore()
    rs.store(1, "irrelevant")
    text = "plain text with no refs"
    assert rs.substitute(text) == "plain text with no refs"


def test_substitute_missing_step_inserts_placeholder():
    rs = ResultStore()
    text = "{RESULT_OF_STEP_5}"
    result = rs.substitute(text)
    assert "5" in result
    assert "not available" in result.lower()


def test_substitute_previous_step_empty_store():
    rs = ResultStore()
    text = "prefix {RESULT_OF_PREVIOUS_STEP} suffix"
    # When store is empty, latest() returns "" so the ref is replaced with ""
    assert rs.substitute(text) == "prefix  suffix"


def test_overwrite_step_result():
    rs = ResultStore()
    rs.store(1, "original")
    rs.store(1, "overwritten")
    assert rs.get(1) == "overwritten"
