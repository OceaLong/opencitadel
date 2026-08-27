"""Application facade for a durable execution command handler."""

from app.application.ports.execution import (
    CommandResult,
    ExecutionCommandHandlerPort,
)
from app.domain.execution.commands import CommandEnvelope


class ExecutionOrchestrator:
    def __init__(self, handler: ExecutionCommandHandlerPort) -> None:
        self._handler = handler

    async def handle(self, command: CommandEnvelope) -> CommandResult:
        return await self._handler.handle(command)


__all__ = ["CommandResult", "ExecutionOrchestrator"]
