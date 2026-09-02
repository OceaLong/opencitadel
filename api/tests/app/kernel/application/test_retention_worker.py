"""Retention worker tests for bounded, hash-authorized purge commands."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.kernel.application.retention_worker import RetentionCandidate, RetentionWorker
from app.kernel.domain.types import OwnerScopeRef, Workflow

NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


class RecordingCommandSink:
    def __init__(self) -> None:
        self.commands = []

    async def submit(self, command, authorization):
        self.commands.append(command)
        return object()


class RetentionStore:
    async def claim_due(self, *, worker_id, now, limit, lease_seconds):
        return (
            RetentionCandidate(
                candidate_id=UUID(int=6300),
                resource_type="run",
                resource_id=str(UUID(int=6301)),
                workflow=Workflow.AGENT,
                owner_scope=OwnerScopeRef.personal("user-1"),
                disposition_hash="a" * 64,
                claim_generation=2,
            ),
        )

    async def mark_completed(self, candidate_id, claim_generation, *, now):
        return True


@pytest.mark.asyncio
async def test_retention_submits_a_bounded_purge_command_with_plan_hash() -> None:
    """Retention must not directly delete content or use an unreviewed plan."""

    sink = RecordingCommandSink()
    worker = RetentionWorker(
        store=RetentionStore(),
        command_sink=sink,
        worker_id="retention-1",
        batch_size=20,
    )

    assert await worker.run_once(now=NOW) == 1
    command = sink.commands[0]
    assert command.type == "PurgeRun"
    assert command.payload["disposition_hash"] == "a" * 64
    assert command.payload["retention_claim_generation"] == 2
