from tests.app.openapi_test_support import app


def test_openapi_exposes_exact_typed_runtime_policy_topology() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/runtime-policies/execution": {"get"},
        "/api/runtime-policies/execution/revisions": {"get", "post"},
        "/api/runtime-policies/execution/revisions/{revision_id}/restore": {"post"},
        "/api/runtime-policies/operations": {"get"},
        "/api/runtime-policies/operations/revisions": {"get", "post"},
        "/api/runtime-policies/operations/revisions/{revision_id}/restore": {"post"},
    }

    assert {path: set(paths[path]) for path in expected} == expected
    assert not any(path.startswith("/api/app-config/runtime-policy") for path in paths)


def test_runtime_policy_mutation_schemas_are_closed_and_actor_free() -> None:
    schemas = app.openapi()["components"]["schemas"]

    for name, policy_field in (
        ("CreateExecutionPolicyRevisionRequest", "policy"),
        ("CreateOperationsPolicyRevisionRequest", "policy"),
    ):
        schema = schemas[name]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "expected_head_version",
            "expected_active_revision_id",
            policy_field,
            "note",
        }
        assert "created_by" not in schema["properties"]

    restore = schemas["RestorePolicyRevisionRequest"]
    assert restore["additionalProperties"] is False
    assert "created_by" not in restore["properties"]
    assert "policy" not in restore["properties"]


def test_execution_and_operations_responses_remain_distinct_and_typed() -> None:
    schemas = app.openapi()["components"]["schemas"]
    execution = schemas["ExecutionPolicyRevisionResponse"]["properties"]["policy"]
    operations = schemas["OperationsPolicyRevisionResponse"]["properties"]["policy"]

    assert execution["$ref"].rsplit("/", 1)[-1] in {
        "ExecutionPolicy",
        "ExecutionPolicy-Output",
    }
    assert operations["$ref"].rsplit("/", 1)[-1] in {
        "OperationsPolicy",
        "OperationsPolicy-Output",
    }
    assert execution != operations
