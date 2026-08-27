"""Authenticated application boundary for the universal Run aggregate."""

from typing import Protocol

from app.application.execution.command_ingress import run_command_envelope
from app.application.execution.orchestrator import CommandResult
from app.domain.execution.commands import (
    CommandContext,
    CommandEnvelope,
    RegisteredCommand,
)


class CommandHandler(Protocol):
    async def handle(self, command: CommandEnvelope) -> CommandResult: ...


class RunService:
    def __init__(self, *, orchestrator: CommandHandler) -> None:
        self._orchestrator = orchestrator

    async def submit(
        self,
        command: RegisteredCommand,
        context: CommandContext,
    ) -> CommandResult:
        return await self._orchestrator.handle(run_command_envelope(command, context))


__all__ = ["RunService"]
