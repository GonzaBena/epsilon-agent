import pytest

from epsilon.settings import jsonc


def test_reads_comments_and_trailing_commas():
    text = '// arriba\n{\n  /* bloque */ "a.b": 1, // fin\n  "c": "x // no es comentario",\n}\n'
    assert jsonc.loads(text) == {"a.b": 1, "c": "x // no es comentario"}


def test_bom_and_crlf():
    assert jsonc.loads('﻿{\r\n "a": true\r\n}\r\n') == {"a": True}


def test_broken_says_the_line():
    with pytest.raises(jsonc.JsoncError) as e:
        jsonc.loads('{\n "a": 1\n "b": 2\n}')
    assert e.value.line == 3


def test_top_level_must_be_object():
    with pytest.raises(jsonc.JsoncError):
        jsonc.loads("[1]")


def test_set_value_keeps_everything_else():
    text = '{\n  // mío\n  "a": 1, // ojo\n  "b": "x"\n}\n'
    out = jsonc.set_value(text, "a", 7)
    assert out == '{\n  // mío\n  "a": 7, // ojo\n  "b": "x"\n}\n'


def test_set_value_adds_before_closing_brace():
    out = jsonc.set_value('{\n  "a": 1 // c\n}\n', "z", [1, 2])
    assert jsonc.loads(out) == {"a": 1, "z": [1, 2]}
    assert "// c" in out


def test_set_value_on_empty_object():
    assert jsonc.loads(jsonc.set_value("{}", "a", "x")) == {"a": "x"}


def test_remove_line_and_comma():
    out = jsonc.remove('{\n  "a": 1,\n  "b": 2\n}\n', "b")
    assert jsonc.loads(out) == {"a": 1}


def test_duplicates_last_wins_and_set_value_cleans():
    text = '{\n "a": 1,\n "a": 2\n}'
    assert jsonc.loads(text) == {"a": 2}
    assert jsonc.duplicates(text) == ["a"]
    out = jsonc.set_value(text, "a", 3)
    assert jsonc.loads(out) == {"a": 3} and jsonc.duplicates(out) == []


def test_crlf_is_kept_on_edit():
    assert "\r\n" in jsonc.set_value('{\r\n "a": 1\r\n}\r\n', "b", 2)


def test_remove_missing_key_changes_nothing():
    text = '{\n  "a": 1\n}\n'
    assert jsonc.remove(text, "zz") == text


def test_nested_values_and_one_line_object():
    out = jsonc.set_value('{"a": {"x": [1, {"y": 2}]}, "b": 1}', "b", 5)
    assert jsonc.loads(out) == {"a": {"x": [1, {"y": 2}]}, "b": 5}
    out = jsonc.set_value('{"a": 1}', "c", True)
    assert jsonc.loads(out) == {"a": 1, "c": True}


def test_set_value_keeps_non_ascii():
    assert "ñ" in jsonc.set_value("{}", "a", "año")
