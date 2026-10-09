from app.main import create_app
from core.config import DeploymentSettings


def test_comparison_fixed_revision_and_diff_job_api_is_exposed():
    schema = create_app(DeploymentSettings(env="test")).openapi()
    for path, method in [
        ("/api/execution-comparisons", "post"),
        ("/api/execution-comparisons/{comparison_id}", "get"),
        ("/api/execution-comparisons/{comparison_id}/refresh", "post"),
        ("/api/execution-comparisons/{comparison_id}/alignments", "post"),
        ("/api/execution-comparisons/{comparison_id}/artifact-diffs", "post"),
        ("/api/execution-analysis/diff-jobs/{job_id}", "get"),
    ]:
        assert method in schema["paths"].get(path, {}), path
        assert "422" not in schema["paths"][path][method]["responses"]

    for name in (
        "ComparisonCreate",
        "ComparisonRefresh",
        "ComparisonAlignment",
        "ComparisonArtifactDiff",
    ):
        assert "request_id" in schema["components"]["schemas"][name]["required"]


def test_canonical_comparison_status_and_explicit_revision_contract():
    schema = create_app(DeploymentSettings(env="test")).openapi()
    paths = schema["paths"]
    assert not any(path.startswith("/api/analysis/") for path in paths)
    assert "201" in paths["/api/execution-comparisons"]["post"]["responses"]
    assert (
        "202"
        in paths["/api/execution-comparisons/{comparison_id}/artifact-diffs"]["post"]["responses"]
    )
    params = {
        p["name"]: p
        for p in paths["/api/execution-comparisons/{comparison_id}"]["get"]["parameters"]
    }
    assert params["revision"]["required"] is True
    assert params["limit"]["schema"]["default"] == 50
    for name in ("ComparisonAlignment", "ComparisonArtifactDiff"):
        assert "revision" in schema["components"]["schemas"][name]["required"]
