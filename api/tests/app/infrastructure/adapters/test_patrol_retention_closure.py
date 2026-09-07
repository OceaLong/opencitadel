"""Exercise retention predicates with real SQL on disposable SQLite shadow tables."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFinding,
    PatrolFindingSeverity,
    PatrolFindingStatus,
    PatrolRemediation,
    PatrolRemediationAction,
    PatrolRemediationStatus,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
)
from app.infrastructure.adapters.query_ports import SqlAlchemyPatrolRetentionStore
from app.infrastructure.models.patrol import (
    PatrolCheckResultModel,
    PatrolFindingModel,
    PatrolRemediationModel,
    PatrolRunModel,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.mark.asyncio
async def test_retention_keeps_unresolved_findings_and_pending_remediation_dependencies(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.infrastructure.adapters.query_ports.configure_session_authorization", AsyncMock()
    )
    metadata = MetaData()
    models = (PatrolRunModel, PatrolFindingModel, PatrolCheckResultModel, PatrolRemediationModel)
    for model in models:
        Table(
            model.__tablename__,
            metadata,
            *[
                Column(
                    column.name,
                    column.type,
                    primary_key=column.primary_key,
                    nullable=column.nullable,
                )
                for column in model.__table__.columns
            ],
        )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    old = datetime(2025, 1, 1, tzinfo=UTC)
    with Session(engine, expire_on_commit=False) as session:
        fixtures = {}
        for label in ("pending", "open", "closed"):
            run = PatrolRun(
                pack_id="pack",
                pack_version=1,
                pack_snapshot={},
                trigger_type=PatrolTriggerType.MANUAL,
                idempotency_key=label,
                execution_run_id=uuid4(),
                status=PatrolRunStatus.COMPLETED,
                finished_at=old,
            )
            check = PatrolCheckResult(
                run_id=run.id,
                check_id="check",
                status=PatrolCheckStatus.FAIL,
                severity=PatrolFindingSeverity.CRITICAL,
                fingerprint=label,
                evidence_refs=[{"ref": "proof"}],
                finished_at=old,
            )
            finding = PatrolFinding(
                run_id=run.id,
                check_result_id=check.id,
                fingerprint=label,
                severity=PatrolFindingSeverity.CRITICAL,
                title=label,
                summary=label,
                status=PatrolFindingStatus.OPEN
                if label == "open"
                else PatrolFindingStatus.RESOLVED,
                first_seen_at=old,
                last_seen_at=old,
            )
            session.add_all(
                [
                    PatrolRunModel.from_domain(run),
                    PatrolCheckResultModel.from_domain(check),
                    PatrolFindingModel.from_domain(finding),
                ]
            )
            if label == "pending":
                remediation = PatrolRemediation(
                    pack_id="pack",
                    run_id=run.id,
                    finding_id=finding.id,
                    check_result_id=check.id,
                    fingerprint=label,
                    action=PatrolRemediationAction.RESTART_WORKLOAD,
                    target_namespace="default",
                    params_hash="hash",
                    idempotency_key="rem",
                    created_by="u",
                    status=PatrolRemediationStatus.EXECUTED,
                )
                session.add(PatrolRemediationModel.from_domain(remediation))
            fixtures[label] = (run, check, finding)
        session.commit()

        class Adapter:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def scalars(self, stmt):
                return session.scalars(stmt)

            async def execute(self, stmt):
                return session.execute(stmt)

            async def commit(self):
                session.commit()

        store = SqlAlchemyPatrolRetentionStore(lambda: Adapter())
        cutoff = datetime(2026, 1, 1, tzinfo=UTC)
        result = await store.cleanup(
            run_cutoff=cutoff, finding_cutoff=cutoff, evidence_cutoff=cutoff, limit=100
        )
        assert result.runs_deleted == 1
        assert result.findings_deleted == 1
        assert result.evidence_refs_purged == 1
        session.expire_all()
        for label in ("pending", "open"):
            run, check, finding = fixtures[label]
            assert session.get(PatrolRunModel, run.id) is not None
            assert session.get(PatrolFindingModel, finding.id) is not None
            assert session.get(PatrolCheckResultModel, check.id).evidence_refs
    engine.dispose()
