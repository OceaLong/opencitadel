import pytest

from app.domain.evaluation.recording import MatchRule, recording_key, sanitize_result


def test_object_order_normalizes_but_array_order_does_not():
    assert recording_key("t", "v", {"a": 1, "b": 2}, "root", 0) == recording_key(
        "t", "v", {"b": 2, "a": 1}, "root", 0
    )
    assert recording_key("t", "v", {"x": [1, 2]}, "root", 0) != recording_key(
        "t", "v", {"x": [2, 1]}, "root", 0
    )


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "u"},
        {"contract": "w"},
        {"branch": "child"},
        {"ordinal": 1},
        {"parallel_group": "g"},
        {"rule_version": 2},
    ],
)
def test_every_fixed_identity_component_changes_key(change):
    values = {
        "tool": "t",
        "contract": "v",
        "args": {"a": 1},
        "branch": "root",
        "ordinal": 0,
        "parallel_group": "",
        "rule_version": 1,
    }
    assert recording_key(**values) != recording_key(**(values | change))


def test_nonfinite_and_boolean_ordinal_are_rejected():
    with pytest.raises(ValueError, match="Out of range float"):
        recording_key("t", "v", {"a": float("nan")}, "root", 0)
    with pytest.raises(ValueError, match="invalid_recording_identity"):
        recording_key("t", "v", {}, "root", True)


def test_exclusions_require_schema_declaration_and_validate_before_excluding():
    schema = {
        "type": "object",
        "properties": {
            "nonce": {"type": "string", "x-recording-nonsemantic": True},
            "amount": {"type": "integer"},
        },
        "required": ["amount"],
        "additionalProperties": False,
    }
    rule = MatchRule(excluded_fields=("nonce",))
    assert rule.normalize({"amount": 2, "nonce": "one"}, schema) == {"amount": 2}
    with pytest.raises(ValueError, match="semantic_argument_exclusion"):
        MatchRule(excluded_fields=("amount",)).normalize({"amount": 2}, schema)
    with pytest.raises(ValueError, match="recording_type_invalid"):
        rule.normalize({"amount": True}, schema)


def test_sanitization_explicit_typed_replacement_and_allowlist():
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}, "secret": {"type": "string"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    assert sanitize_result({"ok": True, "secret": "private"}, schema, ("ok",), {}) == {"ok": True}
    assert sanitize_result({"ok": True}, schema, ("ok",), {"ok": False}) == {"ok": False}
    with pytest.raises(ValueError, match="recording_type_invalid"):
        sanitize_result({"ok": True}, schema, ("ok",), {"ok": "false"})
    with pytest.raises(ValueError, match="invalid_recording_fields"):
        sanitize_result({"ok": True}, schema, ("ok",), {"secret": "test"})


def test_artifact_recording_selection_omits_private_storage_data():
    from app.application.execution.content_sanitization import sanitize_content
    from app.domain.models.tool_result import ToolResult

    result = {"success": True, "data": {"storage_ref": "private-object-location"}}
    schema = ToolResult.model_json_schema()
    public = sanitize_result(result, schema, ("success",), {})
    assert public == {"success": True}
    assert sanitize_content(public) == public
    unsafe = sanitize_result(result, schema, ("success", "data"), {})
    assert sanitize_content(unsafe) != unsafe
