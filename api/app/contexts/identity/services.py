"""Transactional quota and governance services owned by the identity context."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import ConflictError, NotFoundError
from app.domain.runtime_policy.governance import GovernancePolicy, QuotaLimits
from app.kernel.infrastructure.postgres.session_auth import bind_context

from .models import (
    AuditRecordORM,
    GovernancePolicyHeadORM,
    GovernancePolicyRevisionORM,
    TeamORM,
    TeamQuotaORM,
    UserORM,
    UserQuotaORM,
)

QuotaKind = Literal["user", "team"]


def _quota_data(quota: QuotaLimits) -> dict[str, int | None]:
    return {
        "monthlyModelTokens": quota.monthly_model_tokens,
        "dailyNewRuns": quota.daily_new_runs,
        "concurrentRuns": quota.concurrent_runs,
        "storageBytes": quota.storage_bytes,
    }


def _quota_from_mapping(value: dict[str, object]) -> QuotaLimits:
    return QuotaLimits.model_validate(
        {
            "monthly_model_tokens": value.get("monthlyModelTokens"),
            "daily_new_runs": value.get("dailyNewRuns"),
            "concurrent_runs": value.get("concurrentRuns"),
            "storage_bytes": value.get("storageBytes"),
        }
    )


async def _append_audit(
    session: AsyncSession,
    *,
    shard_key: str,
    actor_user_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    metadata: dict[str, object],
    now: datetime,
) -> None:
    """Append one hash-chained record while serializing writers per shard."""
    lock_key = int.from_bytes(hashlib.sha256(shard_key.encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    previous = await session.scalar(
        select(AuditRecordORM)
        .where(AuditRecordORM.shard_key == shard_key)
        .order_by(AuditRecordORM.sequence.desc())
        .limit(1)
    )
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = "0" * 64 if previous is None else previous.hash
    canonical = json.dumps(
        {
            "action": action,
            "actor": actor_user_id,
            "metadata": metadata,
            "previousHash": previous_hash,
            "resourceId": resource_id,
            "resourceType": resource_type,
            "sequence": sequence,
            "shardKey": shard_key,
            "time": now.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    session.add(
        AuditRecordORM(
            id=uuid4(),
            shard_key=shard_key,
            sequence=sequence,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id="",
            metadata_json=metadata,
            previous_hash=previous_hash,
            hash=hashlib.sha256(canonical.encode()).hexdigest(),
            signing_key_id="kernel-v2",
            created_at=now,
        )
    )


class PostgresQuotaService:
    """Read/write the identical four dimensions for personal and team owners."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, kind: QuotaKind, subject_id: str) -> dict[str, int | None]:
        model, key = self._model(kind)
        async with self._session_factory() as session:
            await bind_context(session)
            row = await session.get(model, subject_id)
            if row is not None:
                return _quota_data(
                    QuotaLimits(
                        monthly_model_tokens=row.monthly_model_tokens,
                        daily_new_runs=row.daily_new_runs,
                        concurrent_runs=row.concurrent_runs,
                        storage_bytes=row.storage_bytes,
                    )
                )
            head = await session.get(GovernancePolicyHeadORM, 1)
            if head is None:
                raise RuntimeError("governance policy head is missing")
            revision = await session.get(GovernancePolicyRevisionORM, head.revision_id)
            if revision is None:
                raise RuntimeError("governance policy revision is missing")
            policy = GovernancePolicy.model_validate(revision.policy)
        default = policy.user_quota_defaults if key == "user_id" else policy.team_quota_defaults
        return _quota_data(default)

    async def set(
        self,
        kind: QuotaKind,
        subject_id: str,
        limits: dict[str, object],
        *,
        actor_user_id: str,
    ) -> dict[str, int | None]:
        quota = _quota_from_mapping(limits)
        model, key = self._model(kind)
        owner_model = UserORM if kind == "user" else TeamORM
        values = quota.model_dump()
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            if await session.get(owner_model, subject_id) is None:
                raise NotFoundError(f"{kind} not found")
            await session.execute(
                insert(model)
                .values(**{key: subject_id}, **values, updated_at=now)
                .on_conflict_do_update(
                    index_elements=[getattr(model, key)],
                    set_={**values, "updated_at": now},
                )
            )
            await _append_audit(
                session,
                shard_key=f"{kind}:{subject_id}",
                actor_user_id=actor_user_id,
                action="quota.updated",
                resource_type=f"{kind}_quota",
                resource_id=subject_id,
                metadata=_quota_data(quota),
                now=now,
            )
        return _quota_data(quota)

    @staticmethod
    def _model(kind: QuotaKind):
        if kind == "user":
            return UserQuotaORM, "user_id"
        if kind == "team":
            return TeamQuotaORM, "team_id"
        raise ValueError("unsupported quota owner")


class PostgresGovernanceService:
    """Append policy revisions and atomically move the singleton head."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_active(self) -> dict[str, object]:
        async with self._session_factory() as session:
            await bind_context(session)
            head = await session.get(GovernancePolicyHeadORM, 1)
            if head is None:
                raise RuntimeError("governance policy head is missing")
            revision = await session.get(GovernancePolicyRevisionORM, head.revision_id)
            if revision is None:
                raise RuntimeError("governance policy revision is missing")
        return self._view(head.generation, revision)

    async def update(
        self,
        policy: dict[str, object],
        *,
        expected_generation: int,
        actor_user_id: str,
        note: str,
    ) -> dict[str, object]:
        validated = GovernancePolicy.model_validate(policy)
        now = datetime.now(UTC)
        revision = GovernancePolicyRevisionORM(
            id=uuid4(),
            policy=validated.model_dump(mode="json"),
            digest=validated.digest,
            actor_user_id=actor_user_id,
            note=note,
            created_at=now,
        )
        async with self._session_factory() as session, session.begin():
            await bind_context(session)
            head = await session.scalar(
                select(GovernancePolicyHeadORM)
                .where(GovernancePolicyHeadORM.id == 1)
                .with_for_update()
            )
            if head is None:
                raise RuntimeError("governance policy head is missing")
            if head.generation != expected_generation:
                raise ConflictError("governance policy generation changed")
            session.add(revision)
            head.revision_id = revision.id
            head.generation += 1
            head.updated_at = now
            await _append_audit(
                session,
                shard_key="governance",
                actor_user_id=actor_user_id,
                action="governance.policy.updated",
                resource_type="governance_policy",
                resource_id=str(revision.id),
                metadata={
                    "generation": head.generation,
                    "digest": revision.digest,
                    "note": note,
                },
                now=now,
            )
        return self._view(expected_generation + 1, revision)

    @staticmethod
    def _view(generation: int, revision: GovernancePolicyRevisionORM) -> dict[str, object]:
        return {
            "generation": generation,
            "revisionId": str(revision.id),
            "digest": revision.digest,
            "policy": GovernancePolicy.model_validate(revision.policy).model_dump(
                mode="json", by_alias=True
            ),
            "actorUserId": revision.actor_user_id,
            "note": revision.note,
            "createdAt": revision.created_at.isoformat(),
        }


__all__ = ["PostgresGovernanceService", "PostgresQuotaService", "QuotaKind"]
