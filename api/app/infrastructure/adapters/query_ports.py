"""SQLAlchemy implementations of application query capabilities."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.queries import (
    AuditCountPoint,
    AuditSummary,
    ComplianceEvidenceSnapshot,
    EvidenceSession,
    PatrolRetentionResult,
    QuotaUsageSnapshot,
    UsageBreakdownDimension,
    UsageBreakdownRow,
    UsageSummary,
    UsageTimePoint,
)
from app.infrastructure.models.audit_log import AuditLogORM
from app.infrastructure.models.file import FileModel
from app.infrastructure.models.inference_endpoint import InferenceEndpointORM
from app.infrastructure.models.llm_token_usage import LLMTokenUsageORM
from app.infrastructure.models.patrol import (
    PatrolCheckResultModel,
    PatrolFindingModel,
    PatrolRunModel,
)
from app.infrastructure.models.session import SessionModel
from app.infrastructure.models.user import UserORM
from app.infrastructure.security.db_authorization import configure_session_authorization


class _SqlAlchemyQuery:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory


class SqlAlchemyAuditSummaryQuery(_SqlAlchemyQuery):
    async def summarize(
        self,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> AuditSummary:
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            day_bucket = func.date(AuditLogORM.created_at).label("date")
            day_stmt = (
                select(day_bucket, func.count(AuditLogORM.id))
                .group_by(day_bucket)
                .order_by(day_bucket)
            )
            action_count = func.count(AuditLogORM.id)
            action_stmt = (
                select(AuditLogORM.action, action_count)
                .group_by(AuditLogORM.action)
                .order_by(desc(action_count))
            )
            if start_at is not None:
                day_stmt = day_stmt.where(AuditLogORM.created_at >= start_at)
                action_stmt = action_stmt.where(AuditLogORM.created_at >= start_at)
            if end_at is not None:
                day_stmt = day_stmt.where(AuditLogORM.created_at <= end_at)
                action_stmt = action_stmt.where(AuditLogORM.created_at <= end_at)
            day_rows = (await session.execute(day_stmt)).all()
            action_rows = (await session.execute(action_stmt)).all()
        return AuditSummary(
            by_day=tuple(AuditCountPoint(str(day), int(count or 0)) for day, count in day_rows),
            by_action=tuple(
                AuditCountPoint(str(action), int(count or 0)) for action, count in action_rows
            ),
        )


class SqlAlchemyComplianceEvidenceQuery(_SqlAlchemyQuery):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        login_actions: tuple[str, ...],
        evidence_export_action: str,
        admin_action_prefix: str,
    ) -> None:
        super().__init__(session_factory)
        self._login_actions = login_actions
        self._evidence_export_action = evidence_export_action
        self._admin_action_prefix = admin_action_prefix

    async def collect(
        self,
        *,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> ComplianceEvidenceSnapshot:
        async with self._session_factory() as session:
            await configure_session_authorization(session)

            def window(statement):
                if start_at is not None:
                    statement = statement.where(AuditLogORM.created_at >= start_at)
                if end_at is not None:
                    statement = statement.where(AuditLogORM.created_at <= end_at)
                return statement

            audit_count = int(
                (await session.scalar(window(select(func.count()).select_from(AuditLogORM)))) or 0
            )
            scope_count = int(
                (
                    await session.scalar(
                        window(
                            select(func.count())
                            .select_from(AuditLogORM)
                            .where(AuditLogORM.action == "operator_scope_declared")
                        )
                    )
                )
                or 0
            )
            operator_sessions = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(SessionModel)
                        .where(SessionModel.operator_scope.isnot(None))
                    )
                )
                or 0
            )
            auth_event_count = int(
                (
                    await session.scalar(
                        window(
                            select(func.count())
                            .select_from(AuditLogORM)
                            .where(AuditLogORM.action.in_(self._login_actions))
                        )
                    )
                )
                or 0
            )
            role_rows = (
                await session.execute(
                    select(UserORM.global_role, func.count()).group_by(UserORM.global_role)
                )
            ).all()
            endpoint_rows = (await session.execute(select(InferenceEndpointORM.base_url))).all()
            evidence_export_count = int(
                (
                    await session.scalar(
                        window(
                            select(func.count())
                            .select_from(AuditLogORM)
                            .where(AuditLogORM.action == self._evidence_export_action)
                        )
                    )
                )
                or 0
            )
            admin_action_count = int(
                (
                    await session.scalar(
                        window(
                            select(func.count())
                            .select_from(AuditLogORM)
                            .where(AuditLogORM.action.startswith(self._admin_action_prefix))
                        )
                    )
                )
                or 0
            )
            recent_stmt = (
                window(select(AuditLogORM)).order_by(AuditLogORM.created_at.desc()).limit(20)
            )
            recent = tuple(row.to_domain() for row in (await session.scalars(recent_stmt)).all())
            chain = tuple(
                row.to_domain()
                for row in (
                    await session.scalars(
                        select(AuditLogORM)
                        .where(AuditLogORM.chain_seq.isnot(None))
                        .order_by(AuditLogORM.chain_seq.desc())
                        .limit(20)
                    )
                ).all()[::-1]
            )
        hosts = tuple(
            host
            for (base_url,) in endpoint_rows
            if base_url
            and (
                host := (
                    urlparse(base_url if "://" in base_url else f"//{base_url}").hostname or ""
                ).lower()
            )
        )
        return ComplianceEvidenceSnapshot(
            audit_count=audit_count,
            operator_scope_count=scope_count,
            operator_sessions=operator_sessions,
            auth_event_count=auth_event_count,
            role_distribution={str(role): int(count) for role, count in role_rows},
            inference_endpoint_hosts=hosts,
            evidence_export_count=evidence_export_count,
            admin_action_count=admin_action_count,
            redaction_sample_logs=recent,
            timestamp_chain_logs=chain,
        )


class SqlAlchemyEvidenceSessionQuery(_SqlAlchemyQuery):
    async def list_sessions(self, *, limit: int, offset: int) -> tuple[EvidenceSession, ...]:
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            records = (
                await session.scalars(
                    select(SessionModel)
                    .order_by(SessionModel.updated_at.desc())
                    .offset(max(0, offset))
                    .limit(max(1, min(limit, 200)))
                )
            ).all()
        return tuple(
            EvidenceSession(
                session_id=record.id,
                title=record.title,
                owner_user_id=record.owner_user_id,
                team_id=record.team_id,
                operator_scope=record.operator_scope,
                status=record.status,
                updated_at=record.updated_at,
            )
            for record in records
        )


class SqlAlchemyQuotaUsageQuery(_SqlAlchemyQuery):
    async def snapshot(
        self,
        *,
        user_id: str,
        session_since: datetime,
        token_since: datetime,
    ) -> QuotaUsageSnapshot:
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            daily_sessions = await session.scalar(
                select(func.count(SessionModel.id)).where(
                    SessionModel.owner_user_id == user_id,
                    SessionModel.created_at >= session_since,
                )
            )
            monthly_tokens = await session.scalar(
                select(func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0)).where(
                    LLMTokenUsageORM.owner_user_id == user_id,
                    LLMTokenUsageORM.created_at >= token_since,
                )
            )
            storage_bytes = await session.scalar(
                select(func.coalesce(func.sum(FileModel.size), 0)).where(
                    FileModel.owner_user_id == user_id
                )
            )
        return QuotaUsageSnapshot(
            daily_sessions=int(daily_sessions or 0),
            monthly_tokens=int(monthly_tokens or 0),
            storage_bytes=int(storage_bytes or 0),
        )


class SqlAlchemyUsageQuery(_SqlAlchemyQuery):
    @staticmethod
    def _filters(
        statement,
        *,
        owner_user_id: str | None,
        team_id: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
    ):
        if owner_user_id:
            statement = statement.where(LLMTokenUsageORM.owner_user_id == owner_user_id)
        if team_id:
            statement = statement.where(LLMTokenUsageORM.team_id == team_id)
        if start_at:
            statement = statement.where(LLMTokenUsageORM.created_at >= start_at)
        if end_at:
            statement = statement.where(LLMTokenUsageORM.created_at <= end_at)
        return statement

    async def aggregate(self, **filters) -> UsageSummary:
        statement = self._filters(
            select(
                func.coalesce(func.sum(LLMTokenUsageORM.prompt_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.completion_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.cached_tokens), 0),
                func.count(LLMTokenUsageORM.id),
            ),
            **filters,
        )
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            prompt, completion, total, cached, count = (await session.execute(statement)).one()
        return UsageSummary(
            prompt_tokens=int(prompt or 0),
            completion_tokens=int(completion or 0),
            total_tokens=int(total or 0),
            cached_tokens=int(cached or 0),
            call_count=int(count or 0),
        )

    async def timeseries(self, **filters) -> tuple[UsageTimePoint, ...]:
        day_bucket = func.date(LLMTokenUsageORM.created_at).label("date")
        statement = self._filters(
            select(
                day_bucket,
                func.coalesce(func.sum(LLMTokenUsageORM.prompt_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.completion_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0),
                func.coalesce(func.sum(LLMTokenUsageORM.cached_tokens), 0),
                func.count(LLMTokenUsageORM.id),
            )
            .group_by(day_bucket)
            .order_by(day_bucket),
            **filters,
        )
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            rows = (await session.execute(statement)).all()
        return tuple(
            UsageTimePoint(
                date=str(day),
                prompt_tokens=int(prompt or 0),
                completion_tokens=int(completion or 0),
                total_tokens=int(total or 0),
                cached_tokens=int(cached or 0),
                call_count=int(count or 0),
            )
            for day, prompt, completion, total, cached, count in rows
        )

    async def breakdown(
        self,
        *,
        dimension: UsageBreakdownDimension,
        limit: int,
        **filters,
    ) -> tuple[UsageBreakdownRow, ...]:
        expressions = {
            "model": func.coalesce(LLMTokenUsageORM.model_name, "unknown"),
            "user": func.coalesce(LLMTokenUsageORM.owner_user_id, "unknown"),
            "team": func.coalesce(LLMTokenUsageORM.team_id, "personal"),
            "agent": func.coalesce(LLMTokenUsageORM.agent, "unknown"),
        }
        key = expressions[dimension].label("key")
        total = func.coalesce(func.sum(LLMTokenUsageORM.total_tokens), 0).label("total_tokens")
        calls = func.count(LLMTokenUsageORM.id).label("call_count")
        statement = self._filters(
            select(key, total, calls)
            .group_by(key)
            .order_by(desc(total))
            .limit(max(1, min(limit, 50))),
            **filters,
        )
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            rows = (await session.execute(statement)).all()
        return tuple(
            UsageBreakdownRow(str(key_value), int(tokens or 0), int(call_count or 0))
            for key_value, tokens, call_count in rows
        )


class SqlAlchemyPatrolRetentionStore(_SqlAlchemyQuery):
    async def cleanup(
        self,
        *,
        run_cutoff: datetime,
        finding_cutoff: datetime,
        evidence_cutoff: datetime,
        limit: int,
    ) -> PatrolRetentionResult:
        async with self._session_factory() as session:
            await configure_session_authorization(session)
            finding_ids = list(
                await session.scalars(
                    select(PatrolFindingModel.id)
                    .where(PatrolFindingModel.last_seen_at < finding_cutoff)
                    .order_by(PatrolFindingModel.last_seen_at.asc())
                    .limit(limit)
                )
            )
            if finding_ids:
                await session.execute(
                    delete(PatrolFindingModel).where(PatrolFindingModel.id.in_(finding_ids))
                )
            evidence_result_ids = list(
                await session.scalars(
                    select(PatrolCheckResultModel.id)
                    .join(PatrolRunModel, PatrolRunModel.id == PatrolCheckResultModel.run_id)
                    .where(
                        PatrolRunModel.finished_at.is_not(None),
                        PatrolRunModel.finished_at < evidence_cutoff,
                        PatrolCheckResultModel.evidence_refs != [],
                    )
                    .order_by(PatrolRunModel.finished_at.asc())
                    .limit(limit)
                )
            )
            if evidence_result_ids:
                await session.execute(
                    update(PatrolCheckResultModel)
                    .where(PatrolCheckResultModel.id.in_(evidence_result_ids))
                    .values(evidence_refs=[])
                )
            run_ids = list(
                await session.scalars(
                    select(PatrolRunModel.id)
                    .where(
                        PatrolRunModel.finished_at.is_not(None),
                        PatrolRunModel.finished_at < run_cutoff,
                    )
                    .order_by(PatrolRunModel.finished_at.asc())
                    .limit(limit)
                )
            )
            if run_ids:
                await session.execute(delete(PatrolRunModel).where(PatrolRunModel.id.in_(run_ids)))
            await session.commit()
        return PatrolRetentionResult(
            runs_deleted=len(run_ids),
            findings_deleted=len(finding_ids),
            evidence_refs_purged=len(evidence_result_ids),
        )
