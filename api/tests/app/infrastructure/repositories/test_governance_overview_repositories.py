"""Real-SQL tests for governance repository aggregate methods.
governance dashboard) added to feed GovernanceOverviewService.build_overview.

Mirrors the SQLite-backed fixture pattern in
test_compliance_metrics_repositories.py: real ``app.infrastructure.
repositories.db_*_repository`` classes running against a real (if
lightweight) SQLite engine, on throwaway shadow tables with Postgres-only
DDL (``CURRENT_TIMESTAMP(0)`` defaults, ``::jsonb`` casts, cross-table FKs)
stripped -- the SELECT/GROUP BY/WHERE logic under test is untouched.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import Column, MetaData, Table, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.infrastructure.models.audit_log import AuditLogORM
from app.infrastructure.models.patrol import (
    PatrolFindingModel,
    PatrolRemediationModel,
    PatrolRunModel,
)
from app.infrastructure.repositories.db_audit_repository import DBAuditRepository
from app.infrastructure.repositories.db_patrol_repository import DBPatrolRepository


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self) -> None:
        self._session.flush()


def _shadow_table(orm_cls, metadata: MetaData) -> Table:
    """See test_compliance_metrics_repositories.py's ``_shadow_table`` for
    the full rationale -- same helper, duplicated per this repo's existing
    per-file convention (not shared via conftest)."""
    columns = [
        Column(c.name, c.type, primary_key=c.primary_key, nullable=c.nullable)
        for c in orm_cls.__table__.columns
    ]
    return Table(orm_cls.__table__.name, metadata, *columns)


@pytest.fixture
def db():
    metadata = MetaData()
    for orm_cls in (
        AuditLogORM,
        PatrolRunModel,
        PatrolFindingModel,
        PatrolRemediationModel,
    ):
        _shadow_table(orm_cls, metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        adapter = _AsyncSessionAdapter(session)
        yield SimpleNamespace(
            session=session,
            audit=DBAuditRepository(
                adapter,
                signing_key="governance-audit-signing-key",
                signing_key_id="test",
            ),
            patrol=DBPatrolRepository(adapter),
        )
        session.rollback()
    engine.dispose()


# --- fixture builders: every NOT NULL column set explicitly (shadow tables
# have no server-side defaults). ---


def _audit_log(*, id: str, action: str, created_at: datetime) -> AuditLogORM:
    return AuditLogORM(
        id=id,
        actor_user_id=None,
        actor_ip="",
        action=action,
        resource_type="",
        resource_id="",
        team_id=None,
        request_id="",
        metadata_json={},
        chain_seq=None,
        signing_key_id="primary",
        prev_hash=None,
        entry_hash=None,
        created_at=created_at,
    )


def _patrol_run(*, id: str, created_at: datetime) -> PatrolRunModel:
    return PatrolRunModel(
        id=id,
        pack_id="pack-1",
        session_id=None,
        execution_run_id=uuid5(NAMESPACE_URL, f"test:patrol-run:{id}"),
        pack_version=1,
        pack_snapshot={},
        trigger_type="manual",
        status="completed",
        idempotency_key=f"idem-{id}",
        submission_idempotency_key="",
        collector_capability_hash="",
        started_at=None,
        finished_at=None,
        first_reviewed_at=None,
        duration_ms=None,
        pass_count=0,
        warn_count=0,
        fail_count=0,
        error_count=0,
        skipped_count=0,
        evidence_completeness=None,
        summary={},
        report_artifact_id=None,
        created_at=created_at,
        updated_at=created_at,
    )


def _patrol_finding(*, id: str, run_id: str, first_seen_at: datetime) -> PatrolFindingModel:
    return PatrolFindingModel(
        id=id,
        run_id=run_id,
        check_result_id="check-1",
        fingerprint=f"fp-{id}",
        severity="warning",
        status="open",
        title="t",
        summary="s",
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        occurrence_count=1,
        decided_by=None,
        decided_at=None,
        decision_reason=None,
    )


def _patrol_remediation(*, id: str, status: str, created_at: datetime) -> PatrolRemediationModel:
    return PatrolRemediationModel(
        id=id,
        pack_id="pack-1",
        run_id="run-1",
        finding_id="finding-1",
        check_result_id="check-1",
        fingerprint="fp-1",
        session_id=None,
        action="restart_workload",
        target_namespace="ns1",
        target_workload="",
        target_kind="Deployment",
        params={},
        params_hash="hash1",
        impact_summary="",
        rollback_hint="",
        idempotency_key=f"idem-{id}",
        actuator_capability_hash=None,
        status=status,
        before_observation=None,
        after_observation=None,
        recheck_run_id=None,
        error_code=None,
        error_message=None,
        created_by="u1",
        created_at=created_at,
        updated_at=created_at,
    )


# --- AuditRepository.daily_action_counts ---


@pytest.mark.asyncio
async def test_daily_action_counts_groups_by_date_and_action(db):
    db.session.add_all(
        [
            _audit_log(id="a1", action="agent_tool_approve", created_at=utc(2026, 8, 1, 9)),
            _audit_log(
                id="a2",
                action="agent_tool_approve",
                created_at=utc(2026, 8, 1, 10),
            ),
            _audit_log(id="a3", action="agent_tool_reject", created_at=utc(2026, 8, 1, 11)),
            _audit_log(id="a4", action="agent_tool_approve", created_at=utc(2026, 8, 2, 9)),
            _audit_log(id="a5", action="unrelated", created_at=utc(2026, 8, 1, 12)),
        ]
    )
    db.session.flush()

    rows = await db.audit.daily_action_counts(["agent_tool_approve", "agent_tool_reject"])

    assert sorted(rows, key=lambda r: (r["date"], r["action"])) == [
        {"date": "2026-08-01", "action": "agent_tool_approve", "count": 2},
        {"date": "2026-08-01", "action": "agent_tool_reject", "count": 1},
        {"date": "2026-08-02", "action": "agent_tool_approve", "count": 1},
    ]


@pytest.mark.asyncio
async def test_daily_action_counts_respects_since(db):
    db.session.add_all(
        [
            _audit_log(id="a1", action="agent_tool_denied", created_at=utc(2026, 1, 1)),
            _audit_log(id="a2", action="agent_tool_denied", created_at=utc(2026, 8, 1)),
        ]
    )
    db.session.flush()

    rows = await db.audit.daily_action_counts(["agent_tool_denied"], since=utc(2026, 6, 1))

    assert rows == [{"date": "2026-08-01", "action": "agent_tool_denied", "count": 1}]


@pytest.mark.asyncio
async def test_daily_action_counts_empty_actions_returns_empty_without_querying(db):
    db.session.add(_audit_log(id="a1", action="agent_tool_denied", created_at=utc(2026, 1, 1)))
    db.session.flush()

    assert await db.audit.daily_action_counts([]) == []


# --- PatrolRepository.daily_run_finding_counts ---


@pytest.mark.asyncio
async def test_daily_run_finding_counts_merges_runs_and_findings_by_date(db):
    db.session.add_all(
        [
            _patrol_run(id="r1", created_at=utc(2026, 8, 1, 9)),
            _patrol_run(id="r2", created_at=utc(2026, 8, 1, 10)),
            _patrol_run(id="r3", created_at=utc(2026, 8, 2, 9)),
        ]
    )
    db.session.add_all(
        [
            _patrol_finding(id="f1", run_id="r1", first_seen_at=utc(2026, 8, 1, 9, 5)),
            _patrol_finding(id="f2", run_id="r3", first_seen_at=utc(2026, 8, 3, 0)),
        ]
    )
    db.session.flush()

    rows = await db.patrol.daily_run_finding_counts(utc(2026, 1, 1))

    assert rows == [
        {"date": "2026-08-01", "runs": 2, "findings": 1},
        {"date": "2026-08-02", "runs": 1, "findings": 0},
        {"date": "2026-08-03", "runs": 0, "findings": 1},
    ]


@pytest.mark.asyncio
async def test_daily_run_finding_counts_respects_since_independently_per_series(db):
    db.session.add(_patrol_run(id="r1", created_at=utc(2026, 1, 1)))
    db.session.add(_patrol_finding(id="f1", run_id="r1", first_seen_at=utc(2026, 8, 1)))
    db.session.flush()

    rows = await db.patrol.daily_run_finding_counts(utc(2026, 6, 1))

    assert rows == [{"date": "2026-08-01", "runs": 0, "findings": 1}]


# --- PatrolRepository.remediation_status_counts ---


@pytest.mark.asyncio
async def test_remediation_status_counts_groups_by_status(db):
    db.session.add_all(
        [
            _patrol_remediation(id="m1", status="verified", created_at=utc(2026, 8, 1)),
            _patrol_remediation(id="m2", status="verified", created_at=utc(2026, 8, 2)),
            _patrol_remediation(id="m3", status="failed", created_at=utc(2026, 8, 1)),
        ]
    )
    db.session.flush()

    counts = await db.patrol.remediation_status_counts(utc(2026, 1, 1))

    assert counts == {"verified": 2, "failed": 1}


@pytest.mark.asyncio
async def test_remediation_status_counts_respects_since(db):
    db.session.add_all(
        [
            _patrol_remediation(id="m1", status="verified", created_at=utc(2026, 1, 1)),
            _patrol_remediation(id="m2", status="verified", created_at=utc(2026, 8, 1)),
        ]
    )
    db.session.flush()

    counts = await db.patrol.remediation_status_counts(utc(2026, 6, 1))

    assert counts == {"verified": 1}
