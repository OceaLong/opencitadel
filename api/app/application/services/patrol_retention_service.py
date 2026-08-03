"""Leased-scheduler-compatible Patrol retention cleanup; audit rows are untouched."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import delete, select, update

from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.models.patrol import (
    PatrolCheckResultModel,
    PatrolFindingModel,
    PatrolRunModel,
)


class PatrolRetentionService:
    def __init__(self, uow_factory: Callable[[], IUnitOfWork]) -> None:
        self._uow_factory = uow_factory

    async def cleanup(
        self,
        *,
        run_days: int = 30,
        finding_days: int = 30,
        evidence_days: int = 7,
        batch_size: int = 100,
        now: datetime | None = None,
    ) -> dict[str, int]:
        run_days = min(max(run_days, 1), 90)
        finding_days = min(max(finding_days, 1), 90)
        evidence_days = min(max(evidence_days, 1), 90)
        limit = min(max(batch_size, 1), 1000)
        reference = now or datetime.now(timezone.utc)
        run_cutoff = reference - timedelta(days=run_days)
        finding_cutoff = reference - timedelta(days=finding_days)
        evidence_cutoff = reference - timedelta(days=evidence_days)
        async with self._uow_factory() as uow:
            finding_ids = list((await uow.db_session.execute(
                select(PatrolFindingModel.id)
                .where(PatrolFindingModel.last_seen_at < finding_cutoff)
                .order_by(PatrolFindingModel.last_seen_at.asc())
                .limit(limit)
            )).scalars())
            if finding_ids:
                await uow.db_session.execute(
                    delete(PatrolFindingModel).where(PatrolFindingModel.id.in_(finding_ids))
                )

            evidence_result_ids = list((await uow.db_session.execute(
                select(PatrolCheckResultModel.id)
                .join(PatrolRunModel, PatrolRunModel.id == PatrolCheckResultModel.run_id)
                .where(
                    PatrolRunModel.finished_at.is_not(None),
                    PatrolRunModel.finished_at < evidence_cutoff,
                    PatrolCheckResultModel.evidence_refs != [],
                )
                .order_by(PatrolRunModel.finished_at.asc())
                .limit(limit)
            )).scalars())
            if evidence_result_ids:
                await uow.db_session.execute(
                    update(PatrolCheckResultModel)
                    .where(PatrolCheckResultModel.id.in_(evidence_result_ids))
                    .values(evidence_refs=[])
                )

            run_ids = list((await uow.db_session.execute(
                select(PatrolRunModel.id)
                .where(PatrolRunModel.finished_at.is_not(None), PatrolRunModel.finished_at < run_cutoff)
                .order_by(PatrolRunModel.finished_at.asc())
                .limit(limit)
            )).scalars())
            if run_ids:
                await uow.db_session.execute(
                    delete(PatrolRunModel).where(PatrolRunModel.id.in_(run_ids))
                )
        return {
            "runs_deleted": len(run_ids),
            "findings_deleted": len(finding_ids),
            "evidence_refs_purged": len(evidence_result_ids),
        }
