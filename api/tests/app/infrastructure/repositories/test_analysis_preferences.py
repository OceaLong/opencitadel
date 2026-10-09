"""Real PostgreSQL gates, definitions only while owned infrastructure is unavailable."""

# ruff: noqa: F401,F811
import asyncio

import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.repositories.db_analysis_preferences import DBAnalysisPreferences
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_execution_analysis_repository import (
    analysis_repository,
)

pytestmark = pytest.mark.asyncio


def repository(service):
    base = analysis_repository(service)
    return DBAnalysisPreferences(base.session_factory, signing_secret=base.secret)


async def test_preference_replay_conflict_and_parallel_cas(datasets):
    service, scope, principal, *_ = datasets
    prefs = repository(service)
    assert await prefs.get(scope, principal) == {"timezone": None, "revision": 0}
    first = await prefs.update(
        scope, principal, request_id="first", expected_revision=0, timezone="Asia/Shanghai"
    )
    assert first == {"timezone": "Asia/Shanghai", "revision": 1}
    assert (
        await prefs.update(
            scope, principal, request_id="first", expected_revision=0, timezone="Asia/Shanghai"
        )
        == first
    )
    with pytest.raises(ValueError, match="conflict"):
        await prefs.update(
            scope, principal, request_id="first", expected_revision=0, timezone="UTC"
        )
    result = await asyncio.gather(
        prefs.update(
            scope, principal, request_id="second", expected_revision=1, timezone="Europe/Paris"
        ),
        prefs.update(scope, principal, request_id="third", expected_revision=1, timezone=None),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ValueError) for item in result) == 1
    assert (await prefs.get(scope, principal))["revision"] == 2


async def test_preference_tables_have_no_runtime_raw_privileges(datasets):
    service, scope, principal, *_ = datasets
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        for table in ("analysis_preferences", "analysis_preference_receipts"):
            assert await work.db_session.scalar(
                text(
                    "SELECT NOT has_table_privilege(current_user,:table,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"
                ),
                {"table": table},
            )


async def test_preference_update_rolls_back_receipt_and_value_together(datasets):
    from app.infrastructure.repositories.db_current_authority import DBCurrentAuthority

    service, scope, principal, *_ = datasets
    prefs = repository(service)
    async with prefs.transactions.transaction(scope, principal, current=True) as db:
        signed = await DBCurrentAuthority(db, signing_secret=prefs.secret).signed(
            scope,
            principal,
            operation="update",
            request_id="rolled-back",
            expected_revision=0,
            timezone="Asia/Tokyo",
        )
        assert (
            await db.scalar(
                text("SELECT public.opencitadel_analysis_preference(:body,:signature)"), signed
            )
        )["revision"] == 1
        await db.rollback()
    assert await prefs.get(scope, principal) == {"timezone": None, "revision": 0}
    assert (
        await prefs.update(
            scope, principal, request_id="rolled-back", expected_revision=0, timezone="UTC"
        )
    )["revision"] == 1


async def test_preference_replay_rechecks_current_token(datasets):
    service, scope, principal, *_ = datasets
    prefs = repository(service)
    await prefs.update(scope, principal, request_id="saved", expected_revision=0, timezone="UTC")
    stale = principal.model_copy(update={"token_version": principal.token_version + 1})
    with pytest.raises(PermissionError):
        await prefs.update(scope, stale, request_id="saved", expected_revision=0, timezone="UTC")
