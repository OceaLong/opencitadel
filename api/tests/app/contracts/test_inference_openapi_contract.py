from tests.app.openapi_test_support import app


def test_inference_routes_replace_legacy_llm_routes() -> None:
    paths = app.openapi()["paths"]

    assert "/api/inference/endpoints" in paths
    assert "/api/inference/models" in paths
    assert "/api/inference/bindings/{purpose}" in paths
    assert "/api/inference/status" in paths
    assert "/api/inference/models/{model_id}/probe" in paths
    assert "/api/capabilities" in paths
    assert "/api/a2a" in "\n".join(paths)
    assert not any(path.startswith("/api/llm-") for path in paths)
    assert "/api/llm/status" not in paths


def test_inference_endpoint_schema_never_exposes_credential() -> None:
    schemas = app.openapi()["components"]["schemas"]
    endpoint = schemas["InferenceEndpointResponse"]

    assert "api_key" not in endpoint["properties"]
    assert "credential" not in endpoint["properties"]
    assert endpoint["properties"]["credential_configured"]["type"] == "boolean"


def test_app_config_openapi_has_no_feature_flags() -> None:
    serialized = str(app.openapi()["components"]["schemas"])

    assert "feature_flags" not in serialized
    assert "FeatureFlagsConfig" not in serialized


def test_capability_contract_uses_canonical_closed_state_shape() -> None:
    schemas = app.openapi()["components"]["schemas"]
    state = schemas["CapabilityStateResponse"]

    assert set(state["properties"]) == {"state", "reason_key", "model_id", "details"}
    state_ref = state["properties"]["state"]["$ref"].rsplit("/", 1)[-1]
    assert schemas[state_ref]["enum"] == [
        "available",
        "degraded",
        "not_configured",
        "disabled",
        "denied",
    ]


def test_operational_status_contracts_are_closed_enums() -> None:
    schemas = app.openapi()["components"]["schemas"]

    for schema_name in (
        "HealthStatus",
        "PatrolPackResponse",
        "PatrolRunResponse",
        "PatrolCheckResultResponse",
        "PatrolFindingResponse",
        "PatrolRemediationResponse",
        "EvidenceSessionItem",
    ):
        status = schemas[schema_name]["properties"]["status"]
        assert "$ref" in status, f"{schema_name}.status must be a closed enum"

    scheduled_status = schemas["ScheduledJobResponse"]["properties"]["last_run_status"]
    assert any("$ref" in variant for variant in scheduled_status["anyOf"])
