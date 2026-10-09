"""U06 production proof uses only fully migrated random invocation-owned databases."""

import pytest

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture(autouse=True)
def owned_migration_target_proof(request, isolated_database, record_property):  # noqa: F811
    from sqlalchemy.engine import make_url

    engine, config = isolated_database
    target = make_url(
        config.attributes["deployment_settings"].sqlalchemy_migration_database_uri
    ).database
    assert target == engine.url.database
    assert target.startswith("test_execution_view_")
    assert "_db_schema" not in request.fixturenames
    assert "postgres_integration" not in request.fixturenames
    record_property("owned_migration_target", target)


async def test_public_provenance_exact_cut_and_run_local_persisted_order():
    from tests.app.application.services.test_execution_content_service import (
        test_exact_producer_step_attaches_artifact_only_at_new_cut,
    )

    await test_exact_producer_step_attaches_artifact_only_at_new_cut()


async def test_canonical_source_missing_locator_fallback_and_revocation():
    from tests.app.application.services.test_execution_content_production import (
        test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation,
    )

    await test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation()
