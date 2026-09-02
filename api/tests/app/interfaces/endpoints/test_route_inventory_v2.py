"""The incompatible v2 API exposes only the retained product roots."""

from app.main import create_app

ALLOWED_ROOTS = {
    "admin",
    "approvals",
    "artifacts",
    "auth",
    "files",
    "governance-policy",
    "health",
    "inference",
    "integrations",
    "invitations",
    "knowledge-bases",
    "metrics",
    "notifications",
    "runs",
    "status",
    "teams",
}

RETIRED_FRAGMENTS = {
    "a2a",
    "automation",
    "compliance",
    "memory",
    "patrol",
    "scheduled",
    "service-api",
    "session",
    "share",
    "skill",
    "webhook",
}


def test_openapi_has_exact_retained_roots_and_no_retired_surface() -> None:
    paths = set(create_app().openapi()["paths"])
    roots = {path.removeprefix("/api/").split("/", 1)[0] for path in paths}

    assert roots == ALLOWED_ROOTS
    assert not any(fragment in path for fragment in RETIRED_FRAGMENTS for path in paths)


def test_run_lifecycle_has_commands_but_no_arbitrary_status_write() -> None:
    paths = create_app().openapi()["paths"]

    assert "/api/runs/{run_id}/commands/cancel" in paths
    assert "/api/runs/{run_id}/commands/archive" in paths
    assert "/api/runs/{run_id}/commands/restore" in paths
    assert "patch" not in paths["/api/runs/{run_id}"]
