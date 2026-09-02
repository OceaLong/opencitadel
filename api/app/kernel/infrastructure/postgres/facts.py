"""Database-backed non-deterministic facts frozen into reducer decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.contexts.identity.models import (
    GovernancePolicyHeadORM,
    GovernancePolicyRevisionORM,
    TeamMemberORM,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.runtime_policy.governance import GovernancePolicy
from app.kernel.domain.commands import CommandEnvelope
from app.kernel.domain.decisions import DecisionFacts
from app.kernel.domain.state import RunState

from .session_auth import bind_context


class PostgresDecisionFactsFactory:
    """Resolve policy and reviewers, then allocate enough deterministic inputs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        command: CommandEnvelope,
        state: RunState | None,
    ) -> DecisionFacts:
        del state
        async with self._session_factory() as session:
            await bind_context(session, AuthorizationContext.system("kernel-facts"))
            head = await session.get(GovernancePolicyHeadORM, 1)
            if head is None:
                raise RuntimeError("governance policy head is missing")
            revision = await session.get(GovernancePolicyRevisionORM, head.revision_id)
            if revision is None:
                raise RuntimeError("governance policy revision is missing")
            if command.owner_scope.team_id:
                reviewer_user_ids = tuple(
                    await session.scalars(
                        select(TeamMemberORM.user_id)
                        .where(TeamMemberORM.team_id == command.owner_scope.team_id)
                        .order_by(TeamMemberORM.user_id)
                    )
                )
            else:
                reviewer_user_ids = (command.actor_user_id,)
        policy = GovernancePolicy.model_validate(revision.policy)
        return DecisionFacts(
            now=datetime.now(UTC),
            actor_user_id=command.actor_user_id,
            request_id=command.request_id,
            policy_revision_id=revision.id,
            event_ids=tuple(uuid4() for _ in range(16)),
            effect_ids=tuple(uuid4() for _ in range(4)),
            timer_ids=tuple(uuid4() for _ in range(4)),
            approval_ids=tuple(uuid4() for _ in range(4)),
            reviewer_user_ids=reviewer_user_ids,
            approval_ttl_seconds=policy.approval_ttl_seconds,
        )


__all__ = ["PostgresDecisionFactsFactory"]
