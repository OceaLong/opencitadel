"""Kernel-owned caller-transaction budget preadmission persistence."""

from typing import Protocol
from uuid import UUID

from app.domain.evaluation.budget_binding import BudgetNamespace, BudgetRunBinding
from app.domain.models.scope import OwnerScope


class EvaluationBudgetControlRepository(Protocol):
    async def namespace(
        self, scope: OwnerScope, identity: UUID, *, lock: bool = False
    ) -> BudgetNamespace: ...
    async def create(self, scope: OwnerScope, value: BudgetNamespace) -> BudgetNamespace: ...
    async def binding(self, scope: OwnerScope, run_id: UUID) -> BudgetRunBinding | None: ...
    async def bind(self, scope: OwnerScope, value: BudgetRunBinding) -> BudgetRunBinding: ...
    async def close(
        self, scope: OwnerScope, identity: UUID, *, expected_revision: int
    ) -> BudgetNamespace: ...
