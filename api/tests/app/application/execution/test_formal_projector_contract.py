"""Contracts for projections derived only from the formal event store."""

import inspect
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.execution.events import StoredEvent
from app.infrastructure.execution.models import ExecutionRunProjectionORM
from app.infrastructure.execution.postgres_formal_projector import (
    PostgresFormalProjector as FormalProjector,
)
from app.infrastructure.execution.postgres_run_projection import PostgresRunProjection


def test_formal_projector_reads_one_event_store_and_writes_all_query_models() -> None:
    source = inspect.getsource(FormalProjector)

    assert "PostgresEventStore" in source
    assert "ExecutionRunProjectionORM" in source
    assert "ExecutionResourceBuildProjectionORM" in source
    assert "ExecutionPublicEventORM" in source
    assert "ExecutionActivityProjectionORM" in source
    assert "ExecutionApprovalProjectionORM" in source
    assert "event.public_payload" in source
    assert "session_events" not in source
    assert "execution_facts" not in source
    assert "execution_shadow" not in source


def test_api_run_projection_never_reads_the_private_event_store() -> None:
    source = inspect.getsource(PostgresRunProjection)

    assert "ExecutionEventORM" not in source
    assert "PostgresEventStore" not in source


def test_run_projection_requires_execution_policy_audit_metadata() -> None:
    table = ExecutionRunProjectionORM.__table__

    assert table.c.execution_policy_revision_id.nullable is False
    assert table.c.execution_policy_digest.nullable is False


def test_checkpoint_and_projection_commit_share_one_transaction() -> None:
    source = inspect.getsource(FormalProjector.run_once)

    assert "await session.commit()" in source
    assert source.index("_project_event") < source.index("await session.commit()")


def test_checkpoint_integrity_binds_scope_and_last_position() -> None:
    class _Session:
        record = None

        def add(self, record) -> None:
            self.record = record

    session = _Session()
    FormalProjector._write_checkpoint(
        session,
        checkpoint=None,
        key="user:user-1",
        owner_user_id="user-1",
        team_id=None,
        last_position=17,
    )
    assert session.record is not None
    session.record.last_position = 99

    with pytest.raises(ValueError, match="checkpoint integrity"):
        FormalProjector._validate_checkpoint(session.record, "user:user-1")


def _event(event_type: str, payload: dict) -> StoredEvent:
    return StoredEvent(
        event_id=UUID("91000000-0000-0000-0000-000000000001"),
        stream_type="run",
        stream_id="92000000-0000-0000-0000-000000000001",
        stream_version=1,
        position=1,
        event_type=event_type,
        event_schema_version=1,
        public_payload=payload,
        internal_payload={},
        secret_ref=None,
        owner_user_id="user-1",
        team_id=None,
        correlation_id=UUID("93000000-0000-0000-0000-000000000001"),
        causation_id=None,
        occurred_at=datetime(2026, 8, 24, tzinfo=UTC),
        prev_hash="0" * 64,
        event_hash="a" * 64,
    )


def test_public_projection_exposes_product_events_not_kernel_protocol() -> None:
    created = FormalProjector._public_shape(
        _event(
            "RunCreated",
            {
                "family": "agent",
                "source_entity_type": "session",
                "source_entity_id": "session-1",
                "parent_run_id": None,
                "input": {"role": "user", "message": "hello"},
            },
        )
    )
    completed = FormalProjector._public_shape(
        _event(
            "ActivityCompleted",
            {
                "activity_id": "94000000-0000-0000-0000-000000000001",
                "generation": 0,
                "result_ref": "object://answer",
                "result_summary": "answer",
                "public_data": {
                    "kind": "message",
                    "role": "assistant",
                    "message": "answer",
                },
            },
        )
    )

    assert created is not None
    assert created[0] == "message"
    assert created[1]["role"] == "user"
    assert "policy_snapshot" not in created[1]
    assert completed is not None
    assert completed[0] == "message"
    assert completed[1]["message"] == "answer"
