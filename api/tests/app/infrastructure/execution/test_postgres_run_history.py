"""Real projection boundary for scheduled-job run history (E10b).

Proves ``PostgresRunProjection.list_runs_for_source`` returns the run
projection rows for a source entity, newest-first, honoring pagination,
owner-scope isolation, and surfacing ``failure_code`` from the run state.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine

from app.domain.execution.run import RunFamily, RunState, RunStatus
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.execution.models import ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection
from core.config import load_deployment_settings
from tests.app.execution_test_support import (
    authenticated_session_factory,
    execution_admin_session,
    run_policy_snapshot_json,
)


def _run_row(
    *,
    run_id,
    job_id: str,
    owner_user_id: str,
    status: RunStatus,
    created_at: datetime,
    failure_code: str | None = None,
    source_entity_type: str = "scheduled_job",
) -> ExecutionRunProjectionORM:
    snapshot_json = run_policy_snapshot_json(RunFamily.AUTOMATION)
    correlation_id = uuid4()
    terminal = status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    state = RunState(
        run_id=run_id,
        family=RunFamily.AUTOMATION,
        source_entity_type=source_entity_type,
        source_entity_id=job_id,
        semantic_payload={},
        policy_snapshot=snapshot_json,
        status=status,
        stream_version=2,
        owner_user_id=owner_user_id,
        correlation_id=correlation_id,
        failure_code=failure_code,
    )
    return ExecutionRunProjectionORM(
        run_id=run_id,
        family=RunFamily.AUTOMATION.value,
        source_entity_type=source_entity_type,
        source_entity_id=job_id,
        execution_policy_revision_id=snapshot_json["execution_revision_id"],
        execution_policy_digest=snapshot_json["execution_policy_digest"],
        status=status.value,
        terminal=terminal,
        parent_run_id=None,
        correlation_id=correlation_id,
        owner_user_id=owner_user_id,
        team_id=None,
        stream_version=2,
        last_event_position=2,
        state=state.model_dump(mode="json"),
        state_hash=canonical_state_hash(state),
        last_event_hash="a" * 64,
        created_at=created_at,
        updated_at=created_at,
        terminal_at=created_at if terminal else None,
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_db_schema")
async def test_list_runs_for_source_scopes_orders_and_paginates() -> None:
    owner = f"runhist-owner-{uuid4()}"
    other = f"runhist-other-{uuid4()}"
    job_id = f"runhist-job-{uuid4()}"
    other_job_id = f"runhist-otherjob-{uuid4()}"

    r_old = uuid4()
    r_mid = uuid4()
    r_new = uuid4()
    r_other_owner = uuid4()
    r_other_job = uuid4()
    all_ids = [r_old, r_mid, r_new, r_other_owner, r_other_job]

    async with execution_admin_session() as session:
        session.add(
            _run_row(
                run_id=r_old,
                job_id=job_id,
                owner_user_id=owner,
                status=RunStatus.COMPLETED,
                created_at=datetime(2026, 8, 1, tzinfo=UTC),
            )
        )
        session.add(
            _run_row(
                run_id=r_mid,
                job_id=job_id,
                owner_user_id=owner,
                status=RunStatus.FAILED,
                created_at=datetime(2026, 8, 2, tzinfo=UTC),
                failure_code="ACTIVITY_TIMEOUT",
            )
        )
        session.add(
            _run_row(
                run_id=r_new,
                job_id=job_id,
                owner_user_id=owner,
                status=RunStatus.RUNNING,
                created_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
        )
        # Different owner, same job id -> excluded by scope filter.
        session.add(
            _run_row(
                run_id=r_other_owner,
                job_id=job_id,
                owner_user_id=other,
                status=RunStatus.COMPLETED,
                created_at=datetime(2026, 8, 4, tzinfo=UTC),
            )
        )
        # Same owner, different job id -> excluded by source_entity_id.
        session.add(
            _run_row(
                run_id=r_other_job,
                job_id=other_job_id,
                owner_user_id=owner,
                status=RunStatus.COMPLETED,
                created_at=datetime(2026, 8, 5, tzinfo=UTC),
            )
        )
        await session.commit()

    engine = create_async_engine(load_deployment_settings().sqlalchemy_database_uri)
    session_factory = authenticated_session_factory(
        engine, signing_secret=load_deployment_settings().session_secret
    )
    projection = PostgresRunProjection(
        session_factory=session_factory,
        authorization=AuthorizationContext.system("run-history-test"),
    )
    scope = OwnerScope.personal(owner)
    try:
        runs = await projection.list_runs_for_source(
            source_entity_type="scheduled_job",
            source_entity_id=job_id,
            owner_scope=scope,
            limit=50,
            offset=0,
        )
        # Only this owner's rows for this job, newest first.
        assert [r.run_id for r in runs] == [r_new, r_mid, r_old]
        # Failure code surfaced from run state.
        assert next(r for r in runs if r.run_id == r_mid).failure_code == "ACTIVITY_TIMEOUT"
        assert next(r for r in runs if r.run_id == r_new).terminal_at is None

        # Pagination.
        page = await projection.list_runs_for_source(
            source_entity_type="scheduled_job",
            source_entity_id=job_id,
            owner_scope=scope,
            limit=1,
            offset=1,
        )
        assert [r.run_id for r in page] == [r_mid]
    finally:
        await engine.dispose()
        async with execution_admin_session() as session:
            await session.execute(
                delete(ExecutionRunProjectionORM).where(
                    ExecutionRunProjectionORM.run_id.in_(all_ids)
                )
            )
            await session.commit()
