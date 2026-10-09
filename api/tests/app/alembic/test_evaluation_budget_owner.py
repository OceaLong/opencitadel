# ruff: noqa: F811
import os
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from alembic import command
from app.domain.evaluation.budget import BudgetDemand
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_evaluation_budget_repository import (
    DBEvaluationBudgetRepository,
)
from app.infrastructure.security.db_authorization import configure_session_authorization
from core.config import load_deployment_settings
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401


@pytest.mark.asyncio
async def test_budget_operation_works_with_real_nonbypass_migration_owner(isolated_database):
    engine, config = isolated_database
    owner = os.environ["POSTGRES_MIGRATION_USER"]
    owner_url = engine.url.set(username=owner, password=os.environ["POSTGRES_MIGRATION_PASSWORD"])
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        # This database was created by this exact fixture. No role/global grants.
        quote = connection.dialect.identifier_preparer.quote
        connection.execute(
            sa.text(f"ALTER DATABASE {quote(engine.url.database)} OWNER TO {quote(owner)}")
        )
    settings = config.attributes["deployment_settings"]
    config.attributes["deployment_settings"] = settings.model_copy(
        update={
            "sqlalchemy_migration_database_uri": owner_url.render_as_string(hide_password=False)
        }
    )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(
            sa.text("SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=:owner"),
            {"owner": owner},
        )
    api_url = sa.engine.make_url(load_deployment_settings().sqlalchemy_database_uri).set(
        database=engine.url.database
    )
    runtime = create_async_engine(api_url)
    try:
        async with AsyncSession(runtime) as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("e05-owner-test"),
                signing_secret=settings.database_authorization_signing_secret,
            )
            repo = DBEvaluationBudgetRepository(
                session, signing_secret=settings.database_authorization_signing_secret
            )
            assert (
                await repo.reserve(
                    str(uuid4()),
                    BudgetDemand.model_validate(
                        {
                            "scope": "user:owner-test",
                            "requester": "owner-test",
                            "purpose": "production",
                            "tokens": None,
                            "money": None,
                            "buckets": [{"key": "0:global", "slots": 1}],
                        }
                    ),
                )
            )["fresh"]
            await session.commit()
    finally:
        await runtime.dispose()
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.infrastructure.repositories.db_evaluation_execution_repository import (
        DBEvaluationExecutionRepository,
    )
    from tests.app.execution_test_support import execution_kernel_database_uri

    kernel = create_async_engine(
        sa.engine.make_url(execution_kernel_database_uri()).set(database=engine.url.database)
    )
    try:
        async with AsyncSession(kernel) as session:
            await configure_session_authorization(
                session,
                AuthorizationContext.system("execution-kernel"),
                signing_secret=settings.database_authorization_signing_secret,
            )
            assert await session.scalar(
                sa.text(
                    "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            )
            assert (
                await DBEvaluationExecutionRepository(session).bootstrap(
                    ExecutionSlotPolicy(revision=1)
                )
            ).revision == 1
            await session.commit()
    finally:
        await kernel.dispose()


def migration_module():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "e05_test_migration", Path("alembic/versions/0009evaluation_budget.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("precreated", ["none", "one", "all"])
def test_forward_precreated_and_repeat_migration_preserve_counters(isolated_database, precreated):
    from tests.app.alembic.test_execution_view_migration import _configure_migration

    engine, config = isolated_database
    command.upgrade(config, "0008evaluation_environments")
    module = migration_module()
    with engine.begin() as connection:
        _configure_migration(connection)
        selected = (
            list(module.TABLES)[:1]
            if precreated == "one"
            else list(module.TABLES)
            if precreated == "all"
            else []
        )
        for name in selected:
            connection.execute(sa.text(f"CREATE TABLE {name} ({module.TABLES[name]})"))
        module.upgrade_connection(connection)
        connection.execute(
            sa.text(
                "INSERT INTO evaluation_budget_buckets(key,limits,spent_tokens) VALUES('0:global',CAST(:limits AS jsonb),12)"
            ),
            {"limits": '{"key":"0:global","slots":4}'},
        )
        module.upgrade_connection(connection)
        assert (
            connection.scalar(
                sa.text("SELECT spent_tokens FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 12
        )


def test_drifted_precreated_table_is_rejected_without_repair(isolated_database):
    from tests.app.alembic.test_execution_view_migration import _configure_migration

    engine, config = isolated_database
    command.upgrade(config, "0008evaluation_environments")
    module = migration_module()
    with engine.begin() as connection:
        _configure_migration(connection)
        connection.execute(sa.text("CREATE TABLE evaluation_budget_buckets(key text PRIMARY KEY)"))
        with pytest.raises(RuntimeError, match="schema mismatch"):
            module.upgrade_connection(connection)
