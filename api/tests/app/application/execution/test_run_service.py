"""Application boundary for submitting typed Run commands."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.execution.orchestrator import CommandResult
from app.application.execution.run_service import RunService
from app.domain.execution.commands import CommandContext, RegisteredCommand

NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)


class RecordingOrchestrator:
    def __init__(self) -> None:
        self.commands = []

    async def handle(self, command):
        self.commands.append(command)
        return CommandResult(
            command_id=command.command_id,
            status="accepted",
            first_event_position=10,
            last_event_position=11,
            rejection_code=None,
        )


@pytest.mark.asyncio
async def test_service_constructs_the_only_formal_run_envelope() -> None:
    orchestrator = RecordingOrchestrator()
    service = RunService(orchestrator=orchestrator)
    run_id = UUID("30000000-0000-0000-0000-000000000001")
    command_id = UUID("30000000-0000-0000-0000-000000000002")
    correlation_id = UUID("30000000-0000-0000-0000-000000000003")

    result = await service.submit(
        RegisteredCommand(
            command_id=command_id,
            command_type="StartRun",
            run_id=run_id,
            expected_stream_version=1,
            payload={},
        ),
        CommandContext(
            owner_user_id="user-1",
            team_id=None,
            correlation_id=correlation_id,
            causation_id=None,
            issued_at=NOW,
        ),
    )

    envelope = orchestrator.commands[0]
    assert envelope.stream_type == "run"
    assert envelope.stream_id == str(run_id)
    assert envelope.command_id == command_id
    assert envelope.correlation_id == correlation_id
    assert result.status == "accepted"


def test_command_context_rejects_ambiguous_owner_scope() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        CommandContext(
            owner_user_id="user-1",
            team_id="team-1",
            correlation_id=UUID(int=3),
            causation_id=None,
            issued_at=NOW,
        )
