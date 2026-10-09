"""E07 migration/append boundaries under the existing non-bypass owner role."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
from tests.app.infrastructure.repositories.test_evaluation_score_repository import (
    test_atomic_immutable_source_revisions_and_pending_model as check_append,
)

pytestmark = pytest.mark.asyncio


async def test_score_storage_with_non_bypass_owner_and_api_read_only(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    suites, scope, principal, *_ = budget_binding_fixture
    await check_append(budget_binding_fixture)
    async with suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await work.db_session.scalar(text("SELECT count(*) FROM evaluation_scores")) == 1
        with pytest.raises(DBAPIError, match="permission denied"):
            await work.db_session.execute(text("DELETE FROM evaluation_scores"))
    async with execution_admin_session() as db:
        assert await db.scalar(
            text(
                "SELECT NOT r.rolsuper AND NOT r.rolbypassrls FROM pg_class t JOIN pg_roles r ON r.oid=t.relowner WHERE t.oid='evaluation_scores'::regclass"
            )
        )
        await configure_session_authorization(
            db,
            AuthorizationContext.system("execution-kernel"),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        assert await db.scalar(text("SELECT count(*) FROM evaluation_scores")) == 1
        assert await db.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='evaluation_scores'::regclass AND tgname='e07_immutable' AND tgenabled='O')"
            )
        )
        # Act as the real NOBYPASS table owner: FORCE RLS applies to it, and the
        # admin login behind this session may be a superuser (CI uses postgres).
        owner = await db.scalar(
            text(
                "SELECT r.rolname FROM pg_class t JOIN pg_roles r ON r.oid=t.relowner WHERE t.oid='evaluation_scores'::regclass"
            )
        )
        await db.execute(text(f'SET ROLE "{owner}"'))
        # The non-bypass owner has no UPDATE policy, so normal UPDATE matches
        # zero rows before the immutable trigger can run.
        denied = await db.execute(text("UPDATE evaluation_scores SET reason='overwrite'"))
        assert denied.rowcount == 0
        # A transaction-local probe policy exposes the row to UPDATE and
        # proves the separate immutable trigger also rejects mutation.
        await db.execute(
            text(
                "CREATE POLICY e07_immutable_probe ON evaluation_scores FOR UPDATE "
                "USING (opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' "
                "AND current_setting('app.system_actor',true)='execution-kernel') "
                "WITH CHECK (opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' "
                "AND current_setting('app.system_actor',true)='execution-kernel')"
            )
        )
        with pytest.raises(DBAPIError, match="evaluation_score_history_immutable"):
            await db.execute(text("UPDATE evaluation_scores SET reason='overwrite'"))
