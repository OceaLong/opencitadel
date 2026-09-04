"""Disposable PostgreSQL snapshots backed by replay integrity checks."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.execution.aggregate import ReplaySnapshot
from app.domain.execution.serialization import canonical_state_hash
from app.infrastructure.execution.models import ExecutionSnapshotORM
from app.infrastructure.observability.execution_metrics import record_replay_failure


class PostgresSnapshotStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save[StateT: BaseModel](
        self,
        stream_type: str,
        snapshot: ReplaySnapshot[StateT],
        *,
        owner_user_id: str | None,
        team_id: str | None,
        serializer_version: int,
    ) -> bool:
        if not stream_type.strip():
            raise ValueError("stream_type must not be empty")
        if serializer_version <= 0:
            raise ValueError("serializer_version must be positive")
        if (owner_user_id is None) == (team_id is None):
            raise ValueError("exactly one owner scope is required")
        calculated_hash = canonical_state_hash(snapshot.state)
        if calculated_hash != snapshot.state_hash:
            raise ValueError("snapshot state hash does not match its state")
        statement = (
            pg_insert(ExecutionSnapshotORM)
            .values(
                stream_type=stream_type,
                stream_id=snapshot.stream_id,
                stream_version=snapshot.stream_version,
                owner_user_id=owner_user_id,
                team_id=team_id,
                state=snapshot.state.model_dump(mode="json"),
                state_hash=snapshot.state_hash,
                last_event_hash=snapshot.last_event_hash,
                serializer_version=serializer_version,
            )
            .on_conflict_do_nothing(index_elements=["stream_type", "stream_id", "stream_version"])
            .returning(ExecutionSnapshotORM.stream_version)
        )
        inserted = await self._session.scalar(statement)
        if inserted is not None:
            return True
        existing = await self._session.scalar(
            select(ExecutionSnapshotORM).where(
                ExecutionSnapshotORM.stream_type == stream_type,
                ExecutionSnapshotORM.stream_id == snapshot.stream_id,
                ExecutionSnapshotORM.stream_version == snapshot.stream_version,
            )
        )
        if existing is None:
            raise RuntimeError("snapshot conflict row is not visible")
        persisted = (
            existing.owner_user_id,
            existing.team_id,
            existing.state,
            existing.state_hash,
            existing.last_event_hash,
            existing.serializer_version,
        )
        candidate = (
            owner_user_id,
            team_id,
            snapshot.state.model_dump(mode="json"),
            snapshot.state_hash,
            snapshot.last_event_hash,
            serializer_version,
        )
        if persisted != candidate:
            raise ValueError("snapshot version conflicts with persisted state")
        return False

    async def load[StateT: BaseModel](
        self,
        stream_type: str,
        stream_id: str,
        *,
        state_type: type[StateT],
        serializer_version: int,
        max_stream_version: int | None = None,
    ) -> ReplaySnapshot[StateT] | None:
        if serializer_version <= 0:
            raise ValueError("serializer_version must be positive")
        if max_stream_version is not None and max_stream_version < 0:
            raise ValueError("max_stream_version must not be negative")
        statement = select(ExecutionSnapshotORM).where(
            ExecutionSnapshotORM.stream_type == stream_type,
            ExecutionSnapshotORM.stream_id == stream_id,
        )
        if max_stream_version is not None:
            statement = statement.where(ExecutionSnapshotORM.stream_version <= max_stream_version)
        record = await self._session.scalar(
            statement.order_by(ExecutionSnapshotORM.stream_version.desc()).limit(1)
        )
        if record is None or record.serializer_version != serializer_version:
            return None
        try:
            state = state_type.model_validate(record.state)
        except (TypeError, ValidationError):
            # The state shape no longer parses: schema drifted without a
            # serializer_version bump. Self-heals via replay, but must not be
            # counted as corruption — that alarm is for real integrity damage.
            record_replay_failure("snapshot_schema_drift")
            await self._delete_record(record)
            return None
        if canonical_state_hash(state) != record.state_hash:
            record_replay_failure("snapshot_hash_mismatch")
            await self._delete_record(record)
            return None
        return ReplaySnapshot(
            stream_id=record.stream_id,
            stream_version=record.stream_version,
            state=state,
            state_hash=record.state_hash,
            last_event_hash=record.last_event_hash,
        )

    async def delete(self, stream_type: str, stream_id: str) -> int:
        result = await self._session.execute(
            delete(ExecutionSnapshotORM).where(
                ExecutionSnapshotORM.stream_type == stream_type,
                ExecutionSnapshotORM.stream_id == stream_id,
            )
        )
        return result.rowcount or 0

    async def _delete_record(self, record: ExecutionSnapshotORM) -> None:
        await self._session.execute(
            delete(ExecutionSnapshotORM).where(
                ExecutionSnapshotORM.stream_type == record.stream_type,
                ExecutionSnapshotORM.stream_id == record.stream_id,
                ExecutionSnapshotORM.stream_version == record.stream_version,
            )
        )


__all__ = ["PostgresSnapshotStore"]
