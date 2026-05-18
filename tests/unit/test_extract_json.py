"""
Unit tests for llm/client.py — extract_json, _sanitize_json_text, _clean_parsed_json.
No LLM calls needed: these are pure text-processing functions.
"""
import json
import pytest
from llm.client import extract_json, _sanitize_json_text, _clean_parsed_json


class TestSanitizeJsonText:
    def test_strips_bom(self):
        text = "﻿{\"key\": \"value\"}"
        assert not _sanitize_json_text(text).startswith("﻿")

    def test_removes_line_comments(self):
        text = '{"key": "value"} // trailing comment'
        result = _sanitize_json_text(text)
        assert "//" not in result

    def test_removes_block_comments(self):
        text = '{"key": /* inline */ "value"}'
        result = _sanitize_json_text(text)
        assert "/*" not in result
        assert "*/" not in result

    def test_fixes_trailing_comma_in_object(self):
        text = '{"a": 1, "b": 2,}'
        result = json.loads(_sanitize_json_text(text))
        assert result == {"a": 1, "b": 2}

    def test_fixes_trailing_comma_in_array(self):
        text = "[1, 2, 3,]"
        result = json.loads(_sanitize_json_text(text))
        assert result == [1, 2, 3]

    def test_replaces_python_none(self):
        text = '{"key": None}'
        sanitized = _sanitize_json_text(text)
        assert "None" not in sanitized
        assert "null" in sanitized

    def test_replaces_python_true_false(self):
        text = '{"a": True, "b": False}'
        sanitized = _sanitize_json_text(text)
        assert "True" not in sanitized
        assert "False" not in sanitized
        parsed = json.loads(sanitized)
        assert parsed == {"a": True, "b": False}

    def test_preserves_none_inside_string(self):
        # "None" inside a quoted string value must NOT be replaced
        text = '{"key": "NoneValue"}'
        sanitized = _sanitize_json_text(text)
        assert "NoneValue" in sanitized

    def test_removes_control_chars(self):
        text = '{"key": "val\x01ue"}'
        sanitized = _sanitize_json_text(text)
        assert "\x01" not in sanitized


class TestExtractJson:
    def test_clean_json_object(self):
        assert extract_json('{"key": "value"}') == {"key": "value"}

    def test_clean_json_array(self):
        assert extract_json("[1, 2, 3]") == [1, 2, 3]

    def test_json_in_code_block(self):
        text = '```json\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_json_in_plain_code_block(self):
        text = '```\n{"key": "value"}\n```'
        assert extract_json(text) == {"key": "value"}

    def test_json_embedded_in_prose(self):
        text = 'Here is the result: {"key": "value"} and done.'
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_json_with_trailing_comma(self):
        result = extract_json('{"key": "value",}')
        assert result == {"key": "value"}

    def test_json_with_python_literals(self):
        result = extract_json('{"flag": True, "missing": None}')
        assert result is not None
        assert result["flag"] is True

    def test_returns_none_on_failure(self):
        assert extract_json("this is not json at all") is None

    def test_json_with_line_comment(self):
        text = '{"key": "value" // comment\n}'
        result = extract_json(text)
        assert result is not None
        assert result["key"] == "value"

    def test_nested_json(self):
        text = '{"outer": {"inner": [1, 2, 3]}}'
        assert extract_json(text) == {"outer": {"inner": [1, 2, 3]}}

    def test_empty_object(self):
        assert extract_json("{}") == {}

    def test_empty_string_returns_none(self):
        assert extract_json("") is None


class TestCleanParsedJson:
    def test_removes_none_from_list(self):
        assert _clean_parsed_json([1, None, 2, None, 3]) == [1, 2, 3]

    def test_keeps_none_in_dict_values(self):
        result = _clean_parsed_json({"a": None, "b": 1})
        assert result == {"a": None, "b": 1}

    def test_nested_list_cleanup(self):
        result = _clean_parsed_json({"items": [1, None, 2]})
        assert result == {"items": [1, 2]}

    def test_passthrough_int(self):
        assert _clean_parsed_json(42) == 42

    def test_passthrough_string(self):
        assert _clean_parsed_json("hello") == "hello"

    def test_passthrough_bool(self):
        assert _clean_parsed_json(True) is True

    def test_deeply_nested(self):
        data = {"a": {"b": [1, None, {"c": [None, 2]}]}}
        result = _clean_parsed_json(data)
        assert result == {"a": {"b": [1, {"c": [2]}]}}
