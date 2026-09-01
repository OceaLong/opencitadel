import importlib.util

from app.domain.execution.family import RunFamily
from app.domain.models.resource_bindings import ResourceKind
from app.domain.runtime_policy import ExecutionPolicy
from app.domain.runtime_policy.operations import ResourceGcPolicy
from app.infrastructure.models.registry import model_metadata
from tests.app.openapi_test_support import app


def test_codebase_public_surface_is_absent() -> None:
    document = app.openapi()

    assert not any(path.startswith("/api/codebases") for path in document["paths"])
    assert not any(name.startswith("Codebase") for name in document["components"]["schemas"])


def test_session_and_scheduled_job_requests_do_not_accept_codebase_fields() -> None:
    schemas = app.openapi()["components"]["schemas"]

    for name in (
        "CreateSessionRequest",
        "CreateScheduledJobRequest",
        "UpdateScheduledJobRequest",
    ):
        properties = schemas[name]["properties"]
        assert "codebase_id" not in properties
        assert "codebase_version_id" not in properties


def test_codebase_runtime_and_schema_are_absent() -> None:
    assert {kind.value for kind in ResourceKind} == {"knowledge_base"}
    assert "codebase_ingest" not in {family.value for family in RunFamily}
    assert "codebase" not in ExecutionPolicy.model_fields
    assert "codebase" not in ResourceGcPolicy.model_fields
    assert "codebases" not in model_metadata.tables
    assert not any(name.startswith("codebase_") for name in model_metadata.tables)
    assert "codebase_id" not in model_metadata.tables["scheduled_jobs"].c


def test_codebase_modules_are_not_importable() -> None:
    modules = (
        "app.application.services.codebase_service",
        "app.application.services.codebase_version_service",
        "app.domain.models.codebase",
        "app.domain.models.codebase_version",
        "app.domain.services.codebase",
        "app.domain.services.tools.codebase_tools",
        "app.infrastructure.models.codebase",
        "app.infrastructure.models.codebase_version",
    )
    assert all(importlib.util.find_spec(name) is None for name in modules)
