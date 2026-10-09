from app.main import create_app
from core.config import DeploymentSettings


def test_workbench_contract_is_exposed():
    paths = create_app(DeploymentSettings(env="test")).openapi()["paths"]
    expected = [
        "/execution-runs",
        "/execution-runs/{run_id}/view",
        "/execution-runs/{run_id}/steps",
        "/execution-runs/{run_id}/steps/{step_id}",
        "/execution-runs/{run_id}/events",
        "/execution-runs/{run_id}/events/stream",
        "/execution-runs/{run_id}/timeline",
        "/execution-runs/{run_id}/steps/{step_id}/content",
        "/artifacts/{artifact_id}/provenance",
        "/execution-sources/{citation_id}/content",
        "/execution-artifacts/{artifact_id}/content",
    ]
    for path in expected:
        assert "get" in paths.get("/api" + path, {}), path
        responses = paths["/api" + path]["get"]["responses"]
        assert "422" not in responses
        for status in ("400", "403", "404", "409", "503"):
            assert "schema" in responses[status]["content"]["application/json"]


def test_event_stream_and_binary_download_have_concrete_media_contracts():
    paths = create_app(DeploymentSettings(env="test")).openapi()["paths"]
    stream = paths["/api/execution-runs/{run_id}/events/stream"]["get"]
    assert "text/event-stream" in stream["responses"]["200"]["content"]
    assert any(p["name"] == "Last-Event-ID" and p["in"] == "header" for p in stream["parameters"])
    binary = paths["/api/execution-sources/{citation_id}/download"]["get"]
    assert set(binary["responses"]["200"]["content"]) == {"application/octet-stream"}


def test_run_listing_exposes_paired_source_identity():
    params = create_app(DeploymentSettings(env="test")).openapi()["paths"]["/api/execution-runs"][
        "get"
    ]["parameters"]
    assert {"source_entity_type", "source_entity_id"} <= {p["name"] for p in params}
