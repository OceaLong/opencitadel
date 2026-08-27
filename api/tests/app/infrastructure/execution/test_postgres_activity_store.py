"""Source and model contracts for PostgreSQL Activity lease fencing."""

import inspect

from app.infrastructure.execution.models import ExecutionActivityTaskORM
from app.infrastructure.execution.postgres_activity_store import (
    PostgresActivityStore,
)


def test_activity_model_separates_request_and_claim_generations() -> None:
    columns = ExecutionActivityTaskORM.__table__.columns

    assert "request_generation" in columns
    assert "claim_generation" in columns
    assert "generation" not in columns
    assert "call_started_at" in columns
    assert "completed_at" in columns


def test_claim_query_is_skip_locked_and_every_mutation_is_generation_fenced() -> None:
    source = inspect.getsource(PostgresActivityStore)
    normalized = " ".join(source.split())

    assert "with_for_update(skip_locked=True)" in source
    assert "ExecutionActivityTaskORM.claim_generation == claim.claim_generation" in normalized
    assert "recovered_after_call_started" in source
    assert source.count("await session.commit()") >= 4
