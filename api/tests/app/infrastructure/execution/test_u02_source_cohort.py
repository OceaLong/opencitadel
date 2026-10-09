"""Source selection is captured before paging in a fresh fixture-owned database."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.ports.execution_view import ViewCursorInvalid
from app.domain.models.scope import OwnerScope
from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.execution.test_postgres_execution_view import service, write

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_source_cohort_binds_identity_scope_and_frozen_membership():
    scope, other = OwnerScope.personal(str(uuid4())), OwnerScope.personal(str(uuid4()))
    now = datetime.now(UTC).isoformat()
    matching = sorted([uuid4(), uuid4()], reverse=True)

    async def seed(run, owner, kind="session", identity="s"):
        await write(
            run,
            owner,
            1,
            {
                "family": "agent",
                "status": "running",
                "admitted_at": now,
                "source": {"entity_type": kind, "entity_id": identity},
            },
        )

    for run in matching:
        await seed(run, scope)
    await seed(uuid4(), scope, identity="other")
    await seed(uuid4(), scope, kind="patrol")
    await seed(uuid4(), other)
    api = service()
    filters = {"source_entity_type": "session", "source_entity_id": "s"}
    first = await api.list_runs(scope, filters, limit=1)
    assert [r.run_id for r in first.items] == matching[:1]
    await seed(uuid4(), scope)
    await write(matching[1], scope, 2, {"status": "completed"})
    second = await api.list_runs(scope, filters, limit=1, cursor=first.next_cursor)
    assert [r.run_id for r in second.items] == matching[1:]
    assert second.items[0].status == "running"
    assert second.next_cursor is None
    for changed_scope, changed in [
        (other, filters),
        (scope, {**filters, "source_entity_id": "other"}),
        (scope, {**filters, "source_entity_type": "patrol"}),
    ]:
        with pytest.raises(ViewCursorInvalid):
            await api.list_runs(changed_scope, changed, cursor=first.next_cursor)
