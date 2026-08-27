"""Durable API-side ingress for execution Commands.

The HTTP/application process may enqueue intent and read projections. It does
not run aggregates or append execution events; only the execution-kernel role
does that work.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.application.ports.execution import CommandEnvelopeWriterPort
from app.domain.execution.commands import (
    CommandContext,
    CommandEnvelope,
    RegisteredCommand,
)


class CommandSink(Protocol):
    async def receive(self, command: CommandEnvelope) -> bool: ...


def run_command_envelope(
    command: RegisteredCommand,
    context: CommandContext,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command.command_id,
        command_type=command.command_type,
        command_schema_version=command.command_schema_version,
        stream_type="run",
        stream_id=str(command.run_id),
        expected_stream_version=command.expected_stream_version,
        owner_user_id=context.owner_user_id,
        team_id=context.team_id,
        correlation_id=context.correlation_id,
        causation_id=context.causation_id,
        issued_at=context.issued_at,
        payload=command.payload,
    )


class CommandIngress:
    """Persist Commands without acquiring an orchestration claim."""

    def __init__(
        self,
        *,
        writer: CommandEnvelopeWriterPort,
    ) -> None:
        self._writer = writer

    async def submit(
        self,
        command: RegisteredCommand,
        context: CommandContext,
        *,
        sink: CommandSink | None = None,
    ) -> UUID:
        envelope = run_command_envelope(command, context)
        if sink is not None:
            await sink.receive(envelope)
            return command.command_id
        await self._writer.receive(envelope)
        return command.command_id


__all__ = ["CommandIngress", "CommandSink", "run_command_envelope"]
