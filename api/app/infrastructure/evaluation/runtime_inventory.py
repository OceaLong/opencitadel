"""Kernel-only bounded keyset inventory; no dependency on child Run creation."""

from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_execution_analysis_repository import (
    DBExecutionAnalysisRepository,
)

KERNEL = AuthorizationContext.system("execution-kernel")


class EvaluationRuntimeInventory:
    def __init__(self, uow_factory, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_inventory_limit")
        self.uow_factory, self.limit = uow_factory, limit
        self.after = None

    async def discover(self):
        async with self.uow_factory(KERNEL) as work:
            rows = (
                (
                    await work.db_session.execute(
                        text("""
                SELECT id,scope_body FROM evaluation_batches
                WHERE status IN ('created','validating','queued','running','waiting')
                  AND (CAST(:after AS uuid) IS NULL OR id>CAST(:after AS uuid))
                ORDER BY id LIMIT :limit
            """),
                        {"after": self.after, "limit": self.limit},
                    )
                )
                .mappings()
                .all()
            )
        self.after = rows[-1]["id"] if len(rows) == self.limit else None
        return [(OwnerScope.model_validate(row["scope_body"]), row["id"]) for row in rows]

    async def cleanup_summary(self):
        async with self.uow_factory(KERNEL) as work:
            count = await work.evaluation_summary.cleanup_expired(limit=100)
            count += await DBExecutionAnalysisRepository.cleanup_expired(work.db_session, limit=100)
            await work.commit()
            return count
