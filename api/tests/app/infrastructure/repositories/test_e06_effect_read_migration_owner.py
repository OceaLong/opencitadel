"""Exercise the E06 definer under the existing non-bypass migration role."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import text

from alembic import command
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
    test_effect_read_function_is_scoped_signed_current_and_boolean_only as check_authority,
)
from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
    test_late_physical_unknown_without_run_revision_blocks_retry_and_scoring as check_late,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def fresh_f07_database(isolated_database, monkeypatch):
    from core.config import load_deployment_settings

    engine, config = isolated_database
    role = "opencitadel_migration_runtime"
    name = engine.url.database
    assert name.startswith("test_execution_view_")
    # This changes only the database created by this fixture. No cluster role,
    # membership, password, shared database or E05 policy is changed.
    with engine.begin() as connection:
        assert connection.execute(
            text("SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=:role"),
            {"role": role},
        ).scalar_one()
        connection.execute(text(f'ALTER DATABASE "{name}" OWNER TO "{role}"'))
    settings = config.attributes["deployment_settings"]
    config.attributes["deployment_settings"] = settings.model_copy(
        update={
            "sqlalchemy_migration_database_uri": engine.url.update_query_dict(
                {"options": "-c role=" + role}
            )
            .render_as_string(hide_password=False)
            .replace("%", "%%")
        }
    )
    command.upgrade(config, "head")
    # Administrative fixture seeding still uses its existing admin connection;
    # runtime API and kernel pools continue to use their distinct real logins.
    replacement = load_deployment_settings().model_copy(
        update={
            "sqlalchemy_migration_database_uri": engine.url.render_as_string(hide_password=False)
        }
    )
    monkeypatch.setattr(
        "tests.app.execution_test_support.load_deployment_settings", lambda: replacement
    )


async def test_effect_read_with_real_non_bypass_function_owner(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext

    await check_authority(budget_binding_fixture)
    factory = budget_binding_fixture[-1]
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.db_session.scalar(
            text(
                "SELECT NOT owner.rolsuper AND NOT owner.rolbypassrls AND p.proowner=t.relowner FROM pg_proc p JOIN pg_roles owner ON owner.oid=p.proowner JOIN pg_class t ON t.oid='evaluation_run_lineages'::regclass WHERE p.oid='public.opencitadel_e06_effect_unsafe(text,uuid,boolean,text,text)'::regprocedure"
            )
        )


@pytest.mark.parametrize("terminal", ["failed", "succeeded"])
async def test_late_evidence_visible_through_non_bypass_owner_policies(
    budget_binding_fixture, terminal
):
    await check_late(budget_binding_fixture, terminal)
