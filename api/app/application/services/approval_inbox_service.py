"""Reviewer-facing read model over pending execution approvals."""

from __future__ import annotations

from app.application.ports.queries import ApprovalInboxEntry, RunProjectionPort
from app.domain.models.scope import OwnerScope


class ApprovalInboxService:
    """List approvals awaiting (or already resolved by) a reviewer, by scope."""

    def __init__(self, *, run_projection: RunProjectionPort) -> None:
        self._runs = run_projection

    async def list_approvals(
        self,
        *,
        owner_scope: OwnerScope,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ApprovalInboxEntry, ...]:
        return await self._runs.list_approvals(
            owner_scope=owner_scope,
            status=status,
            limit=limit,
            offset=offset,
        )


__all__ = ["ApprovalInboxService"]
