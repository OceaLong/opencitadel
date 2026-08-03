import pytest

from opencitadel_ops_collector.config import CollectorSettings
from opencitadel_ops_collector.limits import bound


def test_strings_arrays_and_objects_are_bounded():
    settings = CollectorSettings(max_string_chars=256, max_array_items=2, max_rows=2, max_output_bytes=4096)
    value, warnings = bound({"a": "x" * 300, "b": [1, 2, 3], "c": 4}, settings)
    assert len(value["a"]) == 256
    assert value["b"] == [1, 2]
    assert set(warnings) == {"array_truncated", "object_truncated", "string_truncated"}


def test_output_bytes_fail_closed():
    settings = CollectorSettings(max_string_chars=2000, max_output_bytes=1024)
    with pytest.raises(ValueError, match="OUTPUT_TOO_LARGE"):
        bound({"value": "x" * 1500}, settings)


@pytest.mark.parametrize("value", [0, 9])
def test_concurrency_is_bounded_one_to_eight(value):
    with pytest.raises(Exception):
        CollectorSettings(concurrency=value)
