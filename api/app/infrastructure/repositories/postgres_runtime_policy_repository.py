"""Atomic PostgreSQL Runtime Policy repository."""

import hmac
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.execution.commands import require_non_empty
from app.domain.models.authorization import AuthorizationContext
from app.domain.repositories.runtime_policy_repository import RuntimePolicyRepository
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    ActiveOperationsPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    OperationsPolicyRevision,
    RuntimePolicyHead,
    RuntimePolicyHeadConflictError,
    RuntimePolicyIntegrityError,
    RuntimePolicyPair,
    RuntimePolicyUnavailableError,
    policy_digest,
)
from app.infrastructure.models.runtime_policy import (
    ExecutionPolicyRevisionORM,
    OperationsPolicyRevisionORM,
    RuntimePolicyHeadORM,
)
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)


class PostgresRuntimePolicyRepository(RuntimePolicyRepository):
    _SEED_LOCK_ID = 0x5254504F4C494359

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization

    async def seed_if_missing(
        self,
        *,
        execution_policy: ExecutionPolicy,
        operations_policy: OperationsPolicy,
        actor: str,
        note: str,
    ) -> bool:
        normalized_actor, normalized_note = self._normalize_mutation(actor, note)
        async with self._session_factory() as session:
            await self._authorize(session)
            await session.execute(select(func.pg_advisory_xact_lock(self._SEED_LOCK_ID)))
            head_row = await session.get(RuntimePolicyHeadORM, "global")
            if head_row is not None:
                await self._active_pair_from_session(session, head_row)
                return False

            execution_count = await session.scalar(
                select(func.count()).select_from(ExecutionPolicyRevisionORM)
            )
            operations_count = await session.scalar(
                select(func.count()).select_from(OperationsPolicyRevisionORM)
            )
            if execution_count or operations_count:
                raise RuntimePolicyIntegrityError(
                    "Runtime Policy seed found revisions without a head"
                )

            execution_revision = ExecutionPolicyRevisionORM(
                id=uuid4(),
                schema_version=1,
                payload=execution_policy.model_dump(mode="json"),
                digest=policy_digest(1, execution_policy),
                created_by=normalized_actor,
                note=normalized_note,
            )
            operations_revision = OperationsPolicyRevisionORM(
                id=uuid4(),
                schema_version=1,
                payload=operations_policy.model_dump(mode="json"),
                digest=policy_digest(1, operations_policy),
                created_by=normalized_actor,
                note=normalized_note,
            )
            session.add_all((execution_revision, operations_revision))
            await session.flush()
            session.add(
                RuntimePolicyHeadORM(
                    id="global",
                    version=1,
                    execution_revision_id=execution_revision.id,
                    operations_revision_id=operations_revision.id,
                    updated_by=normalized_actor,
                )
            )
            await session.commit()
            return True

    async def load_head(self) -> RuntimePolicyHead:
        async with self._session_factory() as session:
            await self._authorize(session)
            row = await session.get(RuntimePolicyHeadORM, "global")
            return self._head(row)

    async def load_active_pair(self) -> RuntimePolicyPair:
        async with self._session_factory() as session:
            await self._authorize(session)
            head_row = await session.get(RuntimePolicyHeadORM, "global")
            return await self._active_pair_from_session(session, head_row)

    async def load_execution_revision(
        self,
        revision_id: UUID,
    ) -> ExecutionPolicyRevision:
        async with self._session_factory() as session:
            await self._authorize(session)
            row = await session.get(ExecutionPolicyRevisionORM, revision_id)
            return self._execution_revision(row)

    async def load_operations_revision(
        self,
        revision_id: UUID,
    ) -> OperationsPolicyRevision:
        async with self._session_factory() as session:
            await self._authorize(session)
            row = await session.get(OperationsPolicyRevisionORM, revision_id)
            return self._operations_revision(row)

    async def list_execution_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[ExecutionPolicyRevision]:
        self._validate_page(limit=limit, offset=offset)
        async with self._session_factory() as session:
            await self._authorize(session)
            rows = (
                await session.scalars(
                    select(ExecutionPolicyRevisionORM)
                    .order_by(ExecutionPolicyRevisionORM.sequence.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return [self._execution_revision(row) for row in rows]

    async def list_operations_revisions(
        self,
        *,
        limit: int,
        offset: int,
    ) -> list[OperationsPolicyRevision]:
        self._validate_page(limit=limit, offset=offset)
        async with self._session_factory() as session:
            await self._authorize(session)
            rows = (
                await session.scalars(
                    select(OperationsPolicyRevisionORM)
                    .order_by(OperationsPolicyRevisionORM.sequence.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
            return [self._operations_revision(row) for row in rows]

    async def create_and_activate_execution(
        self,
        *,
        policy: ExecutionPolicy,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        actor: str,
        note: str,
        restored_from_id: UUID | None = None,
    ) -> ActiveExecutionPolicy:
        normalized_actor, normalized_note = self._normalize_mutation(actor, note)
        revision = ExecutionPolicyRevisionORM(
            id=uuid4(),
            schema_version=1,
            payload=policy.model_dump(mode="json"),
            digest=policy_digest(1, policy),
            created_by=normalized_actor,
            note=normalized_note,
            restored_from_id=restored_from_id,
        )
        async with self._session_factory() as session:
            await self._authorize(session)
            session.add(revision)
            await session.flush()
            result = await session.execute(
                update(RuntimePolicyHeadORM)
                .where(
                    RuntimePolicyHeadORM.id == "global",
                    RuntimePolicyHeadORM.version == expected_head_version,
                    RuntimePolicyHeadORM.execution_revision_id == expected_active_revision_id,
                )
                .values(
                    version=RuntimePolicyHeadORM.version + 1,
                    execution_revision_id=revision.id,
                    updated_by=normalized_actor,
                    updated_at=func.now(),
                )
                .returning(RuntimePolicyHeadORM)
            )
            head_row = result.scalar_one_or_none()
            if head_row is None:
                current = self._head(await session.get(RuntimePolicyHeadORM, "global"))
                await session.rollback()
                raise RuntimePolicyHeadConflictError(current)
            await session.refresh(revision)
            active = ActiveExecutionPolicy(
                head=self._head(head_row),
                revision=self._execution_revision(revision),
            )
            await session.commit()
            return active

    async def create_and_activate_operations(
        self,
        *,
        policy: OperationsPolicy,
        expected_head_version: int,
        expected_active_revision_id: UUID,
        actor: str,
        note: str,
        restored_from_id: UUID | None = None,
    ) -> ActiveOperationsPolicy:
        normalized_actor, normalized_note = self._normalize_mutation(actor, note)
        revision = OperationsPolicyRevisionORM(
            id=uuid4(),
            schema_version=1,
            payload=policy.model_dump(mode="json"),
            digest=policy_digest(1, policy),
            created_by=normalized_actor,
            note=normalized_note,
            restored_from_id=restored_from_id,
        )
        async with self._session_factory() as session:
            await self._authorize(session)
            session.add(revision)
            await session.flush()
            result = await session.execute(
                update(RuntimePolicyHeadORM)
                .where(
                    RuntimePolicyHeadORM.id == "global",
                    RuntimePolicyHeadORM.version == expected_head_version,
                    RuntimePolicyHeadORM.operations_revision_id == expected_active_revision_id,
                )
                .values(
                    version=RuntimePolicyHeadORM.version + 1,
                    operations_revision_id=revision.id,
                    updated_by=normalized_actor,
                    updated_at=func.now(),
                )
                .returning(RuntimePolicyHeadORM)
            )
            head_row = result.scalar_one_or_none()
            if head_row is None:
                current = self._head(await session.get(RuntimePolicyHeadORM, "global"))
                await session.rollback()
                raise RuntimePolicyHeadConflictError(current)
            await session.refresh(revision)
            active = ActiveOperationsPolicy(
                head=self._head(head_row),
                revision=self._operations_revision(revision),
            )
            await session.commit()
            return active

    async def _authorize(self, session: AsyncSession) -> None:
        await configure_session_authorization(session, self._authorization)

    async def _active_pair_from_session(
        self,
        session: AsyncSession,
        head_row: RuntimePolicyHeadORM | None,
    ) -> RuntimePolicyPair:
        head = self._head(head_row)
        execution_row = await session.get(
            ExecutionPolicyRevisionORM,
            head.execution_revision_id,
        )
        operations_row = await session.get(
            OperationsPolicyRevisionORM,
            head.operations_revision_id,
        )
        execution = self._execution_revision(execution_row)
        operations = self._operations_revision(operations_row)
        return RuntimePolicyPair(
            execution=ActiveExecutionPolicy(head=head, revision=execution),
            operations=ActiveOperationsPolicy(head=head, revision=operations),
        )

    @staticmethod
    def _validate_page(*, limit: int, offset: int) -> None:
        if isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be non-negative")

    @staticmethod
    def _normalize_mutation(actor: str, note: str) -> tuple[str, str]:
        return (
            require_non_empty(actor, field_name="actor"),
            require_non_empty(note, field_name="note"),
        )

    @staticmethod
    def _head(row: RuntimePolicyHeadORM | None) -> RuntimePolicyHead:
        if row is None:
            raise RuntimePolicyUnavailableError("Runtime Policy head is missing")
        return RuntimePolicyHead(
            id="global",
            version=row.version,
            execution_revision_id=row.execution_revision_id,
            operations_revision_id=row.operations_revision_id,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _execution_revision(
        row: ExecutionPolicyRevisionORM | None,
    ) -> ExecutionPolicyRevision:
        if row is None:
            raise RuntimePolicyUnavailableError("Execution Policy revision is missing")
        try:
            policy = ExecutionPolicy.model_validate(row.payload)
            expected_digest = policy_digest(row.schema_version, policy)
            if not hmac.compare_digest(row.digest, expected_digest):
                raise RuntimePolicyIntegrityError(
                    f"Execution Policy revision[{row.id}] digest mismatch"
                )
            return ExecutionPolicyRevision(
                id=row.id,
                sequence=row.sequence,
                schema_version=row.schema_version,
                policy=policy,
                digest=row.digest,
                created_by=row.created_by,
                note=row.note,
                restored_from_id=row.restored_from_id,
                created_at=row.created_at,
            )
        except ValidationError as exc:
            raise RuntimePolicyIntegrityError(
                f"Execution Policy revision[{row.id}] payload is invalid"
            ) from exc

    @staticmethod
    def _operations_revision(
        row: OperationsPolicyRevisionORM | None,
    ) -> OperationsPolicyRevision:
        if row is None:
            raise RuntimePolicyUnavailableError("Operations Policy revision is missing")
        try:
            policy = OperationsPolicy.model_validate(row.payload)
            expected_digest = policy_digest(row.schema_version, policy)
            if not hmac.compare_digest(row.digest, expected_digest):
                raise RuntimePolicyIntegrityError(
                    f"Operations Policy revision[{row.id}] digest mismatch"
                )
            return OperationsPolicyRevision(
                id=row.id,
                sequence=row.sequence,
                schema_version=row.schema_version,
                policy=policy,
                digest=row.digest,
                created_by=row.created_by,
                note=row.note,
                restored_from_id=row.restored_from_id,
                created_at=row.created_at,
            )
        except ValidationError as exc:
            raise RuntimePolicyIntegrityError(
                f"Operations Policy revision[{row.id}] payload is invalid"
            ) from exc


__all__ = ["PostgresRuntimePolicyRepository"]
