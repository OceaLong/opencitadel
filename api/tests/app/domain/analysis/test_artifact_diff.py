"""Bounded whole-input differences, never equality inferred from clipped prefixes."""

from importlib.util import find_spec


def diff():
    assert find_spec("app.domain.analysis.artifact_diff") is not None, "artifact diff is missing"
    from app.domain.analysis import artifact_diff

    return artifact_diff


def test_markdown_diff_preserves_last_line_changes():
    d = diff()
    result = d.compare_text("hello\nold", "hello\nnew")
    assert result.complete
    assert result.content_changed is True
    assert "-old" in result.content
    assert "+new" in result.content


def test_large_equal_prefix_requires_async_whole_input_comparison():
    d = diff()
    result = d.compare_text("a" * 65536 + "old", "a" * 65536 + "new")
    assert result.content_changed is None
    assert not result.complete
    assert result.reason == "async_required"


def test_json_pointer_escape_and_arrays_are_atomic_replacements():
    d = diff()
    result = d.compare_json(
        '{"a/b":{"~x":1},"list":[1,2],"gone":0}', '{"a/b":{"~x":2},"list":[2,1],"new":true}'
    )
    assert result.complete
    assert result.operations == [
        {"op": "remove", "path": "/gone"},
        {"op": "add", "path": "/new", "value": True},
        {"op": "replace", "path": "/a~1b/~0x", "value": 2},
        {"op": "replace", "path": "/list", "value": [2, 1]},
    ]


def test_json_invalid_or_deep_input_not_claimed_complete():
    d = diff()
    for before in (
        '{"truncated":',
        "[" * 70 + "0" + "]" * 70,
        '{"v":NaN}',
        '{"v":1e999}',
        '{"a":1,"a":2}',
    ):
        result = d.compare_json(before, "{}")
        assert not result.complete
        assert result.content_changed is None


def test_json_bool_and_number_are_distinct_values():
    d = diff()
    result = d.compare_json('{"a":true}', '{"a":1}')
    assert result.content_changed is True
    assert result.operations == [{"op": "replace", "path": "/a", "value": 1}]


def test_output_budget_and_adversarial_text_are_explicitly_partial():
    d = diff()
    result = d.compare_text("old\n" * 7000, "new\n" * 7000, output_limit=1024)
    assert len(result.content.encode()) <= 1024
    assert not result.complete
    result = d.compare_json(
        '{"a":"' + "a" * 2000 + '"}', '{"a":"' + "b" * 2000 + '"}', output_limit=100
    )
    assert not result.complete
    assert not result.operations
