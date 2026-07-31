#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.models.tool_approval import (
    ApprovalStatus,
    ToolApprovalBatch,
    ToolApprovalCall,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
    SessionResourceBinding,
    build_phase_regresses,
)
from app.domain.repositories.resource_governance_repository import (
    ResourceGovernanceRepository,
)
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
    SessionResourceBindingORM,
    ToolApprovalBatchORM,
    ToolApprovalCallORM,
)

_TERMINAL_BUILD_STATES = {
    BuildState.SUCCEEDED,
    BuildState.DEGRADED,
    BuildState.FAILED,
    BuildState.CANCELLED,
}
_ALLOWED_BUILD_TRANSITIONS = {
    BuildState.QUEUED: {
        BuildState.QUEUED,
        BuildState.RUNNING,
        BuildState.FAILED,
        BuildState.CANCELLED,
    },
    BuildState.RUNNING: {
        BuildState.RUNNING,
        BuildState.SUCCEEDED,
        BuildState.DEGRADED,
        BuildState.FAILED,
        BuildState.CANCELLED,
    },
}


class DBResourceGovernanceRepository(ResourceGovernanceRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add_build(self, build: ResourceBuild) -> ResourceBuild:
        record = ResourceBuildORM.from_domain(build)
        self.db_session.add(record)
        await self.db_session.flush()
        return record.to_domain()

    async def get_build(
        self,
        build_id: str,
        *,
        for_update: bool = False,
    ) -> Optional[ResourceBuild]:
        stmt = select(ResourceBuildORM).where(
            ResourceBuildORM.id == build_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def get_active_build(
        self,
        resource_kind: ResourceKind,
        resource_id: str,
    ) -> Optional[ResourceBuild]:
        result = await self.db_session.execute(
            select(ResourceBuildORM).where(
                ResourceBuildORM.resource_kind == resource_kind.value,
                ResourceBuildORM.resource_id == resource_id,
                ResourceBuildORM.state.in_(
                    (BuildState.QUEUED.value, BuildState.RUNNING.value)
                ),
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def list_stale_builds(
        self,
        resource_kind: ResourceKind,
        *,
        stale_before: datetime,
        limit: int = 100,
    ) -> list[ResourceBuild]:
        if stale_before.tzinfo is None:
            raise ValueError("stale build cutoff must be timezone-aware")
        if not 1 <= limit <= 500:
            raise ValueError("stale build limit must be between 1 and 500")
        result = await self.db_session.execute(
            select(ResourceBuildORM)
            .where(
                ResourceBuildORM.resource_kind
                == ResourceKind(resource_kind).value,
                ResourceBuildORM.state.in_(
                    (BuildState.QUEUED.value, BuildState.RUNNING.value)
                ),
                func.coalesce(
                    ResourceBuildORM.heartbeat_at,
                    ResourceBuildORM.created_at,
                )
                < stale_before,
            )
            .order_by(
                func.coalesce(
                    ResourceBuildORM.heartbeat_at,
                    ResourceBuildORM.created_at,
                ).asc(),
                ResourceBuildORM.id.asc(),
            )
            .limit(limit)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def append_event(
        self,
        build_id: str,
        event: ResourceBuildEvent,
    ) -> int:
        if event.build_id != build_id:
            raise ValueError("event build identity does not match target build")
        result = await self.db_session.execute(
            select(ResourceBuildORM)
            .where(ResourceBuildORM.id == build_id)
            .with_for_update()
        )
        build = result.scalar_one_or_none()
        if build is None:
            raise ValueError("resource build does not exist")

        current_state = BuildState(build.state)
        if current_state in _TERMINAL_BUILD_STATES:
            previous = await self.get_event(build_id, build.last_event_seq)
            if previous is not None and _same_event_content(previous, event):
                return previous.seq
            raise ValueError("terminal resource build rejects further events")
        if event.state not in _ALLOWED_BUILD_TRANSITIONS[current_state]:
            raise ValueError(
                "invalid resource build state transition "
                f"{current_state.value}->{event.state.value}"
            )
        if not 0.0 <= event.progress <= 1.0:
            raise ValueError("event progress must be between 0 and 1")
        if event.progress < build.progress:
            raise ValueError("event progress cannot move backwards")
        if build_phase_regresses(
            ResourceKind(build.resource_kind),
            build.phase,
            event.phase,
        ):
            raise ValueError("event phase cannot move backwards")

        next_seq = build.last_event_seq + 1
        stored_event = event.model_copy(update={"seq": next_seq})
        self.db_session.add(
            ResourceBuildEventORM.from_domain(stored_event)
        )
        build.state = stored_event.state.value
        build.phase = stored_event.phase
        build.progress = stored_event.progress
        build.heartbeat_at = stored_event.created_at
        build.last_event_seq = next_seq
        metrics = stored_event.payload.get("metrics")
        if isinstance(metrics, dict):
            # Graph checkpoints must survive while the build is still
            # running; terminal publication is not the first durable write.
            build.metrics = dict(metrics)
        if (
            stored_event.state == BuildState.RUNNING
            and build.started_at is None
        ):
            build.started_at = stored_event.created_at
        if stored_event.state in _TERMINAL_BUILD_STATES:
            build.finished_at = stored_event.created_at
            capabilities = stored_event.payload.get("capabilities")
            if isinstance(capabilities, dict):
                build.capabilities = sorted(
                    str(name)
                    for name, available in capabilities.items()
                    if available is True
                )
            elif isinstance(capabilities, list):
                build.capabilities = list(capabilities)
            degraded_reasons = stored_event.payload.get(
                "degraded_reasons"
            )
            if isinstance(degraded_reasons, list):
                build.degraded_reasons = list(
                    dict.fromkeys(
                        str(reason)
                        for reason in degraded_reasons
                        if str(reason).strip()
                    )
                )
            error_code = stored_event.payload.get("error_code")
            error_message = stored_event.payload.get("error_message")
            if error_code is not None:
                build.error_code = str(error_code)
            if error_message is not None:
                build.error_message = str(error_message)
        await self.db_session.flush()
        return next_seq

    async def get_event(
        self,
        build_id: str,
        seq: int,
    ) -> Optional[ResourceBuildEvent]:
        result = await self.db_session.execute(
            select(ResourceBuildEventORM).where(
                ResourceBuildEventORM.build_id == build_id,
                ResourceBuildEventORM.seq == seq,
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def list_events(
        self,
        build_id: str,
        after_seq: int,
        limit: int,
    ) -> list[ResourceBuildEvent]:
        if after_seq < 0:
            raise ValueError("event cursor must be non-negative")
        if not 1 <= limit <= 500:
            raise ValueError("event limit must be between 1 and 500")
        result = await self.db_session.execute(
            select(ResourceBuildEventORM)
            .where(
                ResourceBuildEventORM.build_id == build_id,
                ResourceBuildEventORM.seq > after_seq,
            )
            .order_by(ResourceBuildEventORM.seq.asc())
            .limit(limit)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def add_binding(
        self,
        binding: SessionResourceBinding,
    ) -> SessionResourceBinding:
        record = SessionResourceBindingORM.from_domain(binding)
        self.db_session.add(record)
        await self.db_session.flush()
        return record.to_domain()

    async def get_current_binding(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        *,
        for_update: bool = False,
    ) -> Optional[SessionResourceBinding]:
        stmt = select(SessionResourceBindingORM).where(
            SessionResourceBindingORM.session_id == session_id,
            SessionResourceBindingORM.resource_kind
            == ResourceKind(resource_kind).value,
            SessionResourceBindingORM.is_current.is_(True),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def list_current_bindings(
        self,
        session_id: str,
    ) -> list[SessionResourceBinding]:
        result = await self.db_session.execute(
            select(SessionResourceBindingORM)
            .where(
                SessionResourceBindingORM.session_id == session_id,
                SessionResourceBindingORM.is_current.is_(True),
            )
            .order_by(SessionResourceBindingORM.resource_kind.asc())
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def list_bindings(
        self,
        session_id: str,
        resource_kind: Optional[ResourceKind] = None,
    ) -> list[SessionResourceBinding]:
        stmt = select(SessionResourceBindingORM).where(
            SessionResourceBindingORM.session_id == session_id
        )
        if resource_kind is not None:
            stmt = stmt.where(
                SessionResourceBindingORM.resource_kind
                == ResourceKind(resource_kind).value
            )
        result = await self.db_session.execute(
            stmt.order_by(
                SessionResourceBindingORM.created_at.asc(),
                SessionResourceBindingORM.id.asc(),
            )
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def replace_current_binding(
        self,
        current: SessionResourceBinding,
        replacement: SessionResourceBinding,
    ) -> SessionResourceBinding:
        if (
            replacement.session_id != current.session_id
            or replacement.resource_kind != current.resource_kind
            or replacement.resource_id != current.resource_id
            or replacement.supersedes_binding_id != current.id
            or not replacement.is_current
        ):
            raise ValueError("invalid replacement binding lineage")
        result = await self.db_session.execute(
            select(SessionResourceBindingORM)
            .where(SessionResourceBindingORM.id == current.id)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None or not record.is_current:
            raise ValueError("current binding changed before replacement")
        record.is_current = False
        await self.db_session.flush()
        return await self.add_binding(replacement)

    async def save_approval_batch(self, batch: ToolApprovalBatch) -> None:
        self.db_session.add(ToolApprovalBatchORM.from_domain(batch))
        await self.db_session.flush()

    async def get_pending_approval_batch(
        self, session_id: str
    ) -> Optional[ToolApprovalBatch]:
        stmt = (
            select(ToolApprovalBatchORM)
            .where(
                ToolApprovalBatchORM.session_id == session_id,
                ToolApprovalBatchORM.status == ApprovalStatus.PENDING.value,
            )
            .options(selectinload(ToolApprovalBatchORM.calls))
            .order_by(ToolApprovalBatchORM.created_at.desc())
            .with_for_update()
        )
        result = await self.db_session.execute(stmt)
        now = datetime.now(timezone.utc)
        expired_any = False
        for record in result.scalars().all():
            if not _is_expired(record.expires_at, now):
                if expired_any:
                    await self.db_session.flush()
                return record.to_domain()
            _expire_batch(record, now)
            expired_any = True
        if expired_any:
            await self.db_session.flush()
        return None

    async def get_approval_batch(
        self, batch_id: str
    ) -> Optional[ToolApprovalBatch]:
        result = await self.db_session.execute(
            select(ToolApprovalBatchORM)
            .where(ToolApprovalBatchORM.id == batch_id)
            .options(selectinload(ToolApprovalBatchORM.calls))
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def decide_approval_call(
        self,
        tool_call_id: str,
        status: ApprovalStatus,
        decided_by: str,
    ) -> Optional[ToolApprovalCall]:
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("approval decisions must be approved or rejected")

        batch_id_result = await self.db_session.execute(
            select(ToolApprovalCallORM.batch_id).where(
                ToolApprovalCallORM.tool_call_id == tool_call_id
            )
        )
        batch_id = batch_id_result.scalar_one_or_none()
        if batch_id is None:
            return None

        locked_batch_result = await self.db_session.execute(
            select(ToolApprovalBatchORM)
            .where(ToolApprovalBatchORM.id == batch_id)
            .with_for_update()
        )
        locked_batch_result.scalar_one()
        refreshed_batch_result = await self.db_session.execute(
            select(ToolApprovalBatchORM)
            .where(ToolApprovalBatchORM.id == batch_id)
            .options(selectinload(ToolApprovalBatchORM.calls))
            .execution_options(populate_existing=True)
        )
        batch = refreshed_batch_result.scalar_one()
        record = next(
            call for call in batch.calls if call.tool_call_id == tool_call_id
        )
        now = datetime.now(timezone.utc)

        if record.status in {
            ApprovalStatus.APPROVED.value,
            ApprovalStatus.REJECTED.value,
        }:
            if record.status == status.value:
                return record.to_domain()
            raise ValueError(
                f"approval call {tool_call_id!r} is already decided as "
                f"{record.status!r}"
            )
        if record.status == ApprovalStatus.EXPIRED.value:
            return record.to_domain()

        if batch.status == ApprovalStatus.EXPIRED.value or (
            batch.status != ApprovalStatus.CONSUMED.value
            and _is_expired(batch.expires_at, now)
        ):
            _expire_batch(batch, now)
            await self.db_session.flush()
            return record.to_domain()

        if record.status != ApprovalStatus.PENDING.value:
            raise ValueError(
                f"approval call {tool_call_id!r} is already decided as "
                f"{record.status!r}"
            )

        record.status = status.value
        record.decided_by = decided_by
        record.decided_at = now
        if all(
            call.status != ApprovalStatus.PENDING.value for call in batch.calls
        ):
            batch.status = (
                ApprovalStatus.REJECTED.value
                if any(
                    call.status == ApprovalStatus.REJECTED.value
                    for call in batch.calls
                )
                else ApprovalStatus.APPROVED.value
            )
            batch.decided_at = now
        await self.db_session.flush()
        return record.to_domain()

    async def consume_approval_batch(
        self, batch_id: str
    ) -> Optional[ToolApprovalBatch]:
        stmt = (
            select(ToolApprovalBatchORM)
            .where(ToolApprovalBatchORM.id == batch_id)
            .options(selectinload(ToolApprovalBatchORM.calls))
            .with_for_update()
        )
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        if record.status == ApprovalStatus.CONSUMED.value:
            return record.to_domain()

        now = datetime.now(timezone.utc)
        if _is_expired(record.expires_at, now):
            _expire_batch(record, now)
            await self.db_session.flush()
            return record.to_domain()
        if record.status != ApprovalStatus.APPROVED.value:
            return record.to_domain()
        if not all(
            call.status == ApprovalStatus.APPROVED.value for call in record.calls
        ):
            return record.to_domain()

        record.status = ApprovalStatus.CONSUMED.value
        await self.db_session.flush()
        return record.to_domain().model_copy(
            update={"execution_claimed": True}
        )


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


def _expire_batch(record: ToolApprovalBatchORM, now: datetime) -> None:
    if record.status == ApprovalStatus.CONSUMED.value:
        return
    record.status = ApprovalStatus.EXPIRED.value
    record.decided_at = record.decided_at or now
    for call in record.calls:
        if call.status == ApprovalStatus.PENDING.value:
            call.status = ApprovalStatus.EXPIRED.value
            call.decided_at = call.decided_at or now


def _same_event_content(
    stored: ResourceBuildEvent,
    candidate: ResourceBuildEvent,
) -> bool:
    """Terminal retries are idempotent only for the same semantic event."""
    return (
        stored.build_id == candidate.build_id
        and stored.phase == candidate.phase
        and stored.state == candidate.state
        and stored.progress == candidate.progress
        and stored.payload == candidate.payload
    )
