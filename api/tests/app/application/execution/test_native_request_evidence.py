from copy import deepcopy

import pytest

from app.application.execution.content_sanitization import sanitize_content
from app.application.execution.native_request_evidence import reconstruct_native_request
from app.application.services.execution_usage_service import request_snapshot


@pytest.mark.parametrize(
    "control",
    [
        {"max_tokens": 42, "temperature": 0.2},
        {"max_completion_tokens": 512},
        {"generationConfig": {"maxOutputTokens": 128, "topP": 0.5}},
        {"thinking": {"budget_tokens": 64}},
    ],
)
def test_numerical_controls_survive_public_capture_with_exact_digest(control):
    wire = {"model": "test", "messages": [{"role": "user", "content": "hello"}], **control}
    captured = sanitize_content(request_snapshot(wire))
    assert reconstruct_native_request(captured) == wire


@pytest.mark.parametrize(
    "extra",
    [
        {"vendor_private": "do not retain"},
        {"messages": [{"role": "user", "content": '{"api_key":"sensitive"}'}]},
        {"max_tokens": "sensitive"},
    ],
)
def test_unknown_or_sensitive_content_is_never_reconstructed(extra):
    wire = {"model": "test", "max_tokens": 42, **extra}
    captured = sanitize_content(request_snapshot(wire))
    assert "sensitive" not in str(captured)
    assert reconstruct_native_request(captured) is None


def test_parameter_paths_values_and_digest_cannot_be_forged():
    original = sanitize_content(request_snapshot({"model": "test", "max_tokens": 42}))
    for parameter in (
        {"path": ["api_key"], "value": 42},
        {"path": ["max_tokens"], "value": "secret"},
        {"path": ["max_tokens"], "value": True},
        {"path": ["max_tokens"], "value": 99},
        {"path": ["max_tokens"], "value": float("nan")},
    ):
        altered = deepcopy(original)
        altered["numeric_parameters"] = [parameter]
        assert reconstruct_native_request(altered) is None
    original["numeric_parameters"] *= 2
    assert reconstruct_native_request(original) is None


@pytest.mark.parametrize("value", [None, [], "invalid", {"numeric_parameters": None}])
def test_invalid_capture_shape_is_not_reconstructable(value):
    assert reconstruct_native_request(value) is None
