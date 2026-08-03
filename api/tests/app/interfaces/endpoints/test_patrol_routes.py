from app.main import app


def test_openapi_exposes_complete_patrol_endpoint_set():
    paths = set(app.openapi()["paths"])
    assert {
        "/api/patrol-packs",
        "/api/patrol-packs/{pack_id}",
        "/api/patrol-packs/{pack_id}/metrics",
        "/api/patrol-packs/{pack_id}/validate",
        "/api/patrol-packs/{pack_id}/activate",
        "/api/patrol-packs/{pack_id}/pause",
        "/api/patrol-packs/{pack_id}/trigger",
        "/api/patrol-runs",
        "/api/patrol-runs/{run_id}",
        "/api/patrol-runs/{run_id}/evidence",
        "/api/patrol-runs/{run_id}/cancel",
        "/api/patrol-runs/{run_id}/replay",
        "/api/patrol-findings/{finding_id}/acknowledge",
        "/api/patrol-findings/{finding_id}/resolve",
        "/api/patrol-findings/{finding_id}/false-positive",
    }.issubset(paths)
