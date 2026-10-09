"""Real isolated CPU work yields bounded globally computed output."""

from importlib.util import find_spec

import pytest

pytestmark = pytest.mark.asyncio


def processor():
    assert find_spec("app.infrastructure.execution.comparison_diff_compute") is not None, (
        "isolated diff compute missing"
    )
    from app.infrastructure.execution.comparison_diff_compute import IsolatedDiffCompute

    return IsolatedDiffCompute()


async def test_global_diff_beyond_preview_prefix_is_not_misreported_equal():
    result = await processor().compute(
        b"prefix\n" * 10000 + b"old\n", b"prefix\n" * 10000 + b"new\n", "text"
    )
    assert result["content_changed"] is True
    assert result["complete"] is False or "-old" in result["content"]
    assert len(result["content"].encode()) <= 1048576


async def test_isolated_json_output_preserves_field_diff():
    result = await processor().compute(b'{"a":1}', b'{"a":2}', "json")
    assert result["complete"] is True
    assert result["operations"] == [{"op": "replace", "path": "/a", "value": 2}]


async def test_input_above_worker_budget_never_claims_complete_comparison():
    result = await processor().compute(b"a" * 2097153, b"b", "text")
    assert result["complete"] is False
    assert result["content_changed"] is None
    assert result["reason"] == "input_limit"


async def test_web_diff_sanitizes_previews_in_isolated_process():
    result = await processor().compute(
        b"<script>alert(1)</script><p>old</p>", b'<p onclick="x()">new</p>', "web"
    )
    assert result["content_changed"] is True
    assert "<script" not in result["before_preview"]
    assert "onclick" not in result["after_preview"]
