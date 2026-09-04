"""Read ready Runs from the formal projection for workflow decisions."""

from collections.abc import Collection
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.execution.decision_worker import DecisionCandidate
from app.domain.execution.run import (
    RunState,
    decision_data_digest,
    validated_run_policy_snapshot,
)
from app.domain.execution.serialization import canonical_state_hash
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.models import (
    ExecutionActivityTaskORM,
    ExecutionPoisonedRunORM,
    ExecutionRunProjectionORM,
)
from app.infrastructure.observability.execution_metrics import record_poisoned_run
from app.infrastructure.security.db_authorization import configure_session_authorization


class PostgresRunDecisionSource:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        authorization: AuthorizationContext,
    ) -> None:
        self._session_factory = session_factory
        self._authorization = authorization
        # run_id -> last_event_position of the projection row as loaded by the
        # most recent load_ready batch. disarm() uses it as an optimistic guard:
        # a row the projector re-armed from a *newer* event between load and
        # disarm keeps its decision_due_at (losing that wakeup would strand the
        # Run, since nothing else re-arms an unchanged projection).
        self._armed_positions: dict[UUID, int] = {}

    async def load_ready(self, *, limit: int) -> tuple[DecisionCandidate, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            # Already-quarantined Runs are excluded so a poison row is skipped on
            # every subsequent scan instead of re-poisoning the batch forever.
            quarantined = select(ExecutionPoisonedRunORM.run_id)
            # Decision-readiness filter (D4 / P0-1): only rows the projector
            # armed (queued, or running with no active activities) are decoded.
            # A thousand WAITING(approval) Runs no longer cost this scan
            # anything — they carry decision_due_at IS NULL until an approval /
            # timer event re-arms them.
            records = tuple(
                (
                    await session.scalars(
                        select(ExecutionRunProjectionORM)
                        .where(
                            ExecutionRunProjectionORM.terminal.is_(False),
                            ExecutionRunProjectionORM.decision_due_at.is_not(None),
                            ExecutionRunProjectionORM.decision_due_at <= now,
                            ExecutionRunProjectionORM.run_id.not_in(quarantined),
                        )
                        .order_by(
                            ExecutionRunProjectionORM.decision_due_at,
                            ExecutionRunProjectionORM.run_id,
                        )
                        .limit(limit)
                    )
                ).all()
            )
            self._armed_positions = {
                record.run_id: record.last_event_position for record in records
            }
            decoded: list[tuple[ExecutionRunProjectionORM, RunState]] = []
            quarantined_any = False
            for record in records:
                try:
                    decoded.append((record, self._decode(record)))
                except (ValidationError, ValueError) as exc:
                    # Poison-row isolation: one corrupt projection must not abort
                    # the whole batch (which previously crashed every replica).
                    # Quarantine it, count it, and keep processing the rest.
                    await self._quarantine(session, record, exc)
                    record_poisoned_run()
                    quarantined_any = True
                    continue
            payloads = await self._load_decision_payloads(session, decoded)
            candidates: list[DecisionCandidate] = []
            for record, state in decoded:
                try:
                    candidates.append(
                        DecisionCandidate(
                            state=state,
                            decision_payloads=self._verified_payloads(state, payloads),
                        )
                    )
                except ValueError as exc:
                    await self._quarantine(session, record, exc)
                    record_poisoned_run()
                    quarantined_any = True
            if quarantined_any:
                await session.commit()
        return tuple(candidates)

    async def disarm(self, run_ids: Collection[UUID]) -> None:
        """Clear decision_due_at for Runs the planner just found idle.

        Guarded by the last_event_position observed at load time: if the formal
        projector advanced the row (and possibly re-armed it) after this batch
        was loaded, the newer arming wins and the row stays scheduled. Without
        the guard a stale disarm could strand a Run forever, because nothing
        re-arms a projection row that receives no further events.
        """
        if not run_ids:
            return
        async with self._session_factory() as session:
            await configure_session_authorization(session, self._authorization)
            for run_id in run_ids:
                loaded_position = self._armed_positions.get(run_id)
                if loaded_position is None:
                    continue
                await session.execute(
                    update(ExecutionRunProjectionORM)
                    .where(
                        ExecutionRunProjectionORM.run_id == run_id,
                        ExecutionRunProjectionORM.last_event_position <= loaded_position,
                    )
                    .values(decision_due_at=None)
                )
            await session.commit()

    @staticmethod
    async def _load_decision_payloads(
        session: AsyncSession,
        decoded: list[tuple[ExecutionRunProjectionORM, RunState]],
    ) -> dict[object, dict]:
        """Batch-load off-stream decision payloads for the current generation.

        Only succeeded activities whose settlement event recorded a digest are
        loaded (in practice: model calls with tool decisions), so the query
        stays small even for large batches.
        """
        needed = [
            activity_id
            for _, state in decoded
            for activity_id, generation, _, _, digest in state.activity_results
            if generation == state.retry_generation and digest is not None
        ]
        if not needed:
            return {}
        rows = (
            await session.execute(
                select(
                    ExecutionActivityTaskORM.activity_id,
                    ExecutionActivityTaskORM.decision_payload,
                ).where(
                    ExecutionActivityTaskORM.activity_id.in_(needed),
                    ExecutionActivityTaskORM.status == "succeeded",
                )
            )
        ).all()
        return dict(rows)

    @staticmethod
    def _verified_payloads(
        state: RunState,
        payloads: dict[object, dict],
    ) -> dict[object, dict]:
        """Bind loaded payloads back to the hash-chained history via digests."""
        verified: dict[object, dict] = {}
        for activity_id, generation, _, _, digest in state.activity_results:
            if generation != state.retry_generation or digest is None:
                continue
            payload = payloads.get(activity_id)
            if payload is None:
                raise ValueError(
                    f"decision payload for activity {activity_id} is missing "
                    "from execution_activity_tasks"
                )
            if decision_data_digest(payload) != digest:
                raise ValueError(f"decision payload digest mismatch for activity {activity_id}")
            verified[activity_id] = payload
        return verified

    @staticmethod
    def _decode(record: ExecutionRunProjectionORM) -> RunState:
        state = RunState.model_validate(record.state)
        snapshot = validated_run_policy_snapshot(state)
        if canonical_state_hash(state) != record.state_hash:
            raise ValueError("execution_run_projection state hash mismatch")
        if (
            snapshot.execution_revision_id != record.execution_policy_revision_id
            or snapshot.execution_policy_digest != record.execution_policy_digest
        ):
            raise ValueError("execution_run_projection policy metadata mismatch")
        return state

    @staticmethod
    async def _quarantine(
        session: AsyncSession,
        record: ExecutionRunProjectionORM,
        error: Exception,
    ) -> None:
        now = datetime.now(UTC)
        reason = type(error).__name__
        detail = str(error)[:2000] or reason
        statement = (
            pg_insert(ExecutionPoisonedRunORM)
            .values(
                run_id=record.run_id,
                owner_user_id=record.owner_user_id,
                team_id=record.team_id,
                reason=reason[:128],
                last_error=detail,
                first_seen_at=now,
                last_seen_at=now,
            )
            .on_conflict_do_update(
                index_elements=["run_id"],
                set_={
                    "reason": reason[:128],
                    "last_error": detail,
                    "last_seen_at": now,
                },
            )
        )
        await session.execute(statement)


__all__ = ["PostgresRunDecisionSource"]
