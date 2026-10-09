"""Real judge admission under the existing non-bypass migration owner."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_judge_repository import (
    test_schedule_admits_dedicated_ask_and_leaves_subject_unchanged as check_admit,
)

pytestmark = pytest.mark.asyncio


async def test_judge_storage_non_bypass_owner_and_private_api_boundary(budget_binding_fixture):
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    suites, scope, principal, *_ = budget_binding_fixture
    await check_admit(budget_binding_fixture)
    async with suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(DBAPIError, match="permission denied"):
            await work.db_session.execute(text("SELECT * FROM evaluation_judge_intents"))
    async with execution_admin_session() as db:
        assert await db.scalar(
            text(
                "SELECT NOT r.rolsuper AND NOT r.rolbypassrls AND t.relforcerowsecurity FROM pg_class t JOIN pg_roles r ON r.oid=t.relowner WHERE t.oid='evaluation_judge_intents'::regclass"
            )
        )
        await configure_session_authorization(
            db,
            AuthorizationContext.system("execution-kernel"),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        assert await db.scalar(text("SELECT count(*) FROM evaluation_judge_intents")) == 1
        with pytest.raises(DBAPIError, match="evaluation_score_history_immutable"):
            await db.execute(text("UPDATE evaluation_judge_intents SET protocol=protocol"))
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_judge_work WHERE status='submitted'")
            )
            == 1
        )
        with pytest.raises(DBAPIError, match="judge_work_identity_immutable"):
            await work.db_session.execute(
                text("UPDATE evaluation_judge_work SET status='pending' WHERE status='submitted'")
            )
