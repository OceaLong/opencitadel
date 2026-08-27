"""PostgreSQL Patrol repository with workspace scoping."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.patrol import (
    PATROL_REMEDIATION_TERMINAL_STATUSES,
    PatrolCheckResult,
    PatrolFinding,
    PatrolPack,
    PatrolRemediation,
    PatrolRun,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.patrol_repository import PatrolRepository
from app.infrastructure.models.patrol import (
    PatrolCheckResultModel,
    PatrolFindingModel,
    PatrolPackModel,
    PatrolRemediationModel,
    PatrolRunModel,
)


class DBPatrolRepository(PatrolRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def _run_scope(self, run: PatrolRun) -> OwnerScope:
        pack = await self.db_session.get(PatrolPackModel, run.pack_id)
        if pack is None:
            raise ValueError("patrol run pack does not exist")
        if pack.team_id:
            return OwnerScope.team(pack.owner_user_id, pack.team_id)
        return OwnerScope.personal(pack.owner_user_id)

    @staticmethod
    def _scope_pack(stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            return stmt.where(PatrolPackModel.team_id == scope.team_id)
        return stmt.where(
            PatrolPackModel.owner_user_id == scope.user_id, PatrolPackModel.team_id.is_(None)
        )

    def _scope_run(self, stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        return self._scope_pack(
            stmt.join(PatrolPackModel, PatrolPackModel.id == PatrolRunModel.pack_id), scope
        )

    async def save_pack(self, pack: PatrolPack) -> PatrolPack:
        current = await self.db_session.get(PatrolPackModel, pack.id)
        if current is None:
            current = PatrolPackModel.from_domain(pack)
            self.db_session.add(current)
        else:
            current.update_from_domain(pack)
        await self.db_session.flush()
        return current.to_domain()

    async def get_pack(
        self, pack_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolPack | None:
        stmt = self._scope_pack(
            select(PatrolPackModel).where(
                PatrolPackModel.id == pack_id, PatrolPackModel.deleted_at.is_(None)
            ),
            scope,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        return row.to_domain() if row else None

    async def list_packs(
        self, scope: OwnerScope, *, limit: int = 20, offset: int = 0
    ) -> list[PatrolPack]:
        stmt = (
            self._scope_pack(
                select(PatrolPackModel).where(PatrolPackModel.deleted_at.is_(None)), scope
            )
            .order_by(PatrolPackModel.updated_at.desc())
            .limit(min(max(limit, 1), 100))
            .offset(max(offset, 0))
        )
        return [row.to_domain() for row in (await self.db_session.execute(stmt)).scalars().all()]

    async def save_run(
        self,
        run: PatrolRun,
        *,
        parent_run_id: UUID | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
    ) -> PatrolRun:
        del parent_run_id, correlation_id, causation_id
        current = await self.db_session.get(PatrolRunModel, run.id)
        if current is None:
            current = PatrolRunModel.from_domain(run)
            self.db_session.add(current)
        else:
            current.update_from_domain(run)
        await self.db_session.flush()
        return current.to_domain()

    async def get_run(
        self, run_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolRun | None:
        stmt = self._scope_run(select(PatrolRunModel).where(PatrolRunModel.id == run_id), scope)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        return row.to_domain() if row else None

    async def get_run_by_session_id(self, session_id: str) -> PatrolRun | None:
        row = (
            await self.db_session.execute(
                select(PatrolRunModel).where(PatrolRunModel.session_id == session_id)
            )
        ).scalar_one_or_none()
        return row.to_domain() if row else None

    async def get_run_by_idempotency_key(self, key: str) -> PatrolRun | None:
        row = (
            await self.db_session.execute(
                select(PatrolRunModel).where(PatrolRunModel.idempotency_key == key)
            )
        ).scalar_one_or_none()
        return row.to_domain() if row else None

    async def get_active_run_for_pack(self, pack_id: str) -> PatrolRun | None:
        row = (
            await self.db_session.execute(
                select(PatrolRunModel).where(
                    PatrolRunModel.pack_id == pack_id,
                    PatrolRunModel.status.in_(("queued", "running")),
                )
            )
        ).scalar_one_or_none()
        return row.to_domain() if row else None

    async def list_runs(
        self,
        scope: OwnerScope,
        *,
        pack_id: str | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[PatrolRun]:
        stmt = self._scope_run(select(PatrolRunModel), scope)
        if pack_id:
            stmt = stmt.where(PatrolRunModel.pack_id == pack_id)
        if status:
            stmt = stmt.where(PatrolRunModel.status == status)
        if created_from:
            stmt = stmt.where(PatrolRunModel.created_at >= created_from)
        if created_to:
            stmt = stmt.where(PatrolRunModel.created_at <= created_to)
        stmt = (
            stmt.order_by(PatrolRunModel.created_at.desc())
            .limit(min(max(limit, 1), 100))
            .offset(max(offset, 0))
        )
        return [row.to_domain() for row in (await self.db_session.execute(stmt)).scalars().all()]

    async def save_check_results(self, items: list[PatrolCheckResult]) -> list[PatrolCheckResult]:
        if len({item.run_id for item in items}) > 1:
            raise ValueError("patrol check result batch must belong to one run")
        saved: list[PatrolCheckResult] = []
        for item in items:
            stmt = select(PatrolCheckResultModel).where(
                PatrolCheckResultModel.run_id == item.run_id,
                PatrolCheckResultModel.check_id == item.check_id,
            )
            current = (await self.db_session.execute(stmt)).scalar_one_or_none()
            if current is None:
                current = PatrolCheckResultModel.from_domain(item)
                self.db_session.add(current)
            saved.append(item)
        await self.db_session.flush()
        return saved

    async def list_check_results(
        self, run_id: str, scope: OwnerScope | None = None
    ) -> list[PatrolCheckResult]:
        if scope is not None and await self.get_run(run_id, scope) is None:
            return []
        rows = (
            (
                await self.db_session.execute(
                    select(PatrolCheckResultModel)
                    .where(PatrolCheckResultModel.run_id == run_id)
                    .order_by(
                        PatrolCheckResultModel.started_at.asc(),
                        PatrolCheckResultModel.check_id.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        return [row.to_domain() for row in rows]

    async def save_finding(self, finding: PatrolFinding) -> PatrolFinding:
        current = await self.db_session.get(PatrolFindingModel, finding.id)
        if current is None:
            current = PatrolFindingModel.from_domain(finding)
            self.db_session.add(current)
        else:
            current.update_from_domain(finding)
        await self.db_session.flush()
        return current.to_domain()

    async def get_finding(
        self, finding_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolFinding | None:
        stmt = select(PatrolFindingModel).join(
            PatrolRunModel, PatrolRunModel.id == PatrolFindingModel.run_id
        )
        stmt = self._scope_run(stmt, scope).where(PatrolFindingModel.id == finding_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        return row.to_domain() if row else None

    async def list_findings(
        self, run_id: str, scope: OwnerScope | None = None
    ) -> list[PatrolFinding]:
        if scope is not None and await self.get_run(run_id, scope) is None:
            return []
        rows = (
            (
                await self.db_session.execute(
                    select(PatrolFindingModel)
                    .where(PatrolFindingModel.run_id == run_id)
                    .order_by(
                        PatrolFindingModel.severity.desc(), PatrolFindingModel.last_seen_at.desc()
                    )
                )
            )
            .scalars()
            .all()
        )
        return [row.to_domain() for row in rows]

    async def get_open_finding_by_fingerprint(self, fingerprint: str) -> PatrolFinding | None:
        row = (
            await self.db_session.execute(
                select(PatrolFindingModel)
                .where(
                    PatrolFindingModel.fingerprint == fingerprint,
                    PatrolFindingModel.status.in_(("open", "acknowledged")),
                )
                .order_by(PatrolFindingModel.last_seen_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return row.to_domain() if row else None

    async def save_remediation(self, remediation: PatrolRemediation) -> PatrolRemediation:
        current = await self.db_session.get(PatrolRemediationModel, remediation.id)
        if current is None:
            current = PatrolRemediationModel.from_domain(remediation)
            self.db_session.add(current)
        else:
            current.update_from_domain(remediation)
        await self.db_session.flush()
        return current.to_domain()

    async def get_remediation(
        self, remediation_id: str, scope: OwnerScope | None = None, *, for_update: bool = False
    ) -> PatrolRemediation | None:
        stmt = select(PatrolRemediationModel).join(
            PatrolRunModel, PatrolRunModel.id == PatrolRemediationModel.run_id
        )
        stmt = self._scope_run(stmt, scope).where(PatrolRemediationModel.id == remediation_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        return row.to_domain() if row else None

    async def list_remediations_for_run(
        self, run_id: str, scope: OwnerScope | None = None
    ) -> list[PatrolRemediation]:
        if scope is not None and await self.get_run(run_id, scope) is None:
            return []
        rows = (
            (
                await self.db_session.execute(
                    select(PatrolRemediationModel)
                    .where(PatrolRemediationModel.run_id == run_id)
                    .order_by(PatrolRemediationModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [row.to_domain() for row in rows]

    async def get_active_remediation_for_finding(self, finding_id: str) -> PatrolRemediation | None:
        terminal_values = tuple(status.value for status in PATROL_REMEDIATION_TERMINAL_STATUSES)
        stmt = (
            select(PatrolRemediationModel)
            .where(
                PatrolRemediationModel.finding_id == finding_id,
                PatrolRemediationModel.status.notin_(terminal_values),
            )
            .order_by(PatrolRemediationModel.created_at.desc())
            .limit(1)
        )
        row = (await self.db_session.execute(stmt)).scalar_one_or_none()
        return row.to_domain() if row else None

    async def get_remediation_by_session_id(self, session_id: str) -> PatrolRemediation | None:
        row = (
            await self.db_session.execute(
                select(PatrolRemediationModel).where(
                    PatrolRemediationModel.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        return row.to_domain() if row else None

    async def get_remediation_by_recheck_run_id(self, run_id: str) -> PatrolRemediation | None:
        row = (
            await self.db_session.execute(
                select(PatrolRemediationModel).where(
                    PatrolRemediationModel.recheck_run_id == run_id
                )
            )
        ).scalar_one_or_none()
        return row.to_domain() if row else None

    async def daily_run_finding_counts(self, since: datetime) -> list[dict]:
        run_date_col = func.date(PatrolRunModel.created_at)
        runs_stmt = (
            select(run_date_col.label("date"), func.count())
            .where(PatrolRunModel.created_at >= since)
            .group_by(run_date_col)
        )
        runs_by_date = {
            str(date_value): int(count)
            for date_value, count in (await self.db_session.execute(runs_stmt)).all()
        }

        # "findings" counts new findings by first_seen_at (when a fingerprint
        # is first observed), not by their owning run's created_at -- a run
        # started on one day can still be evaluated (and its findings
        # recorded) after midnight for long-running packs.
        finding_date_col = func.date(PatrolFindingModel.first_seen_at)
        findings_stmt = (
            select(finding_date_col.label("date"), func.count())
            .where(PatrolFindingModel.first_seen_at >= since)
            .group_by(finding_date_col)
        )
        findings_by_date = {
            str(date_value): int(count)
            for date_value, count in (await self.db_session.execute(findings_stmt)).all()
        }

        dates = sorted(set(runs_by_date) | set(findings_by_date))
        return [
            {
                "date": date,
                "runs": runs_by_date.get(date, 0),
                "findings": findings_by_date.get(date, 0),
            }
            for date in dates
        ]

    async def remediation_status_counts(self, since: datetime) -> dict[str, int]:
        stmt = (
            select(PatrolRemediationModel.status, func.count())
            .where(PatrolRemediationModel.created_at >= since)
            .group_by(PatrolRemediationModel.status)
        )
        return {status: int(count) for status, count in (await self.db_session.execute(stmt)).all()}
