"""Kernel caller-UoW execution preadmission; no public state or release grant."""

from typing import Any, Protocol
from uuid import UUID

from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
from app.domain.models.scope import OwnerScope


class EvaluationExecutionRepository(Protocol):
    async def prepare(
        self, scope: OwnerScope, run_id: UUID, policy: ExecutionSlotPolicy
    ) -> dict[str, Any]: ...

    async def withdraw_unaccepted(
        self, scope: OwnerScope, run_id: UUID, command_id: UUID, policy: ExecutionSlotPolicy
    ) -> bool: ...
