"""Real-SQL repro for the F3 finding in task-2-report.md.

PatrolPackService.create_pack() does:

    await uow.scheduled_job.save(job)   # DBScheduledJobRepository.save(): add(), no flush
    await uow.patrol.save_pack(pack)    # DBPatrolRepository.save_pack(): add(); await flush()

pack.scheduled_job_id references job.id via a Postgres FK
(patrol_packs_scheduled_job_id_fkey, see infrastructure/models/patrol.py:69). The two
ORM classes (ScheduledJobModel, PatrolPackModel) have a Column-level ForeignKey but no
`relationship()` between them, so SQLAlchemy's flush-time insert-ordering (driven by
`relationship()`-derived mapper dependencies, not bare Column FKs) has no reason to
insert scheduled_jobs before patrol_packs when both objects are still pending in the
same Session at the moment `save_pack()`'s own `flush()` runs. Confirmed against a
real opencitadel-postgres container while smoke-testing Task 2's seed script
end-to-end (docker compose --profile patrol --profile demo): the pack insert failed
with `ForeignKeyViolationError: ... patrol_packs_scheduled_job_id_fkey`.

This file mirrors the SQLite-backed real-SQL fixture pattern in
test_compliance_metrics_repositories.py / test_db_tool_approval_repository.py
(create_engine + _AsyncSessionAdapter running the real DB<n>Repository classes), but
unlike those files it deliberately *keeps* the one FK under test (scheduled_job_id ->
scheduled_jobs.id) on the shadow table and turns on `PRAGMA foreign_keys=ON`, so a real
FK check runs. SQLite's FK enforcement is immediate (not deferred) by default, same as
Postgres here, and SQLAlchemy's flush-ordering logic is DB-agnostic (it runs entirely
in the ORM unit-of-work before any SQL is emitted) -- so if this test goes green
against SQLite it is because the *insert order* is actually fixed, not because of a
SQLite-specific leniency. Verified pre-fix: this test fails with the same
IntegrityError/ForeignKeyViolation shape as the real Postgres run.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.application.patrol_templates import load_patrol_template
from app.domain.models.patrol import PatrolPack
from app.domain.models.scheduled_job import ScheduledJob
from app.infrastructure.models.patrol import PatrolPackModel
from app.infrastructure.models.scheduled_job import ScheduledJobModel
from app.infrastructure.repositories.db_patrol_repository import DBPatrolRepository
from app.infrastructure.repositories.db_scheduled_job_repository import (
    DBScheduledJobRepository,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _AsyncSessionAdapter:
    """Only adapts SQLAlchemy's local sync Session call shape to AsyncSession.

    Both repositories under test call `db_session.get(...)` (not just
    `execute`/`add`/`flush`), unlike the audit/user/session repos the sibling
    fixtures adapt, so `get` is included here.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, instance) -> None:
        self._session.add(instance)

    async def get(self, model, ident):
        return self._session.get(model, ident)

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self) -> None:
        self._session.flush()


def _shadow_table(orm_cls, metadata: MetaData, *, fk: dict[str, str] | None = None) -> Table:
    """Rebuild an ORM class's table (name + column names/types/primary keys) on a
    throwaway MetaData. Every Column-level ForeignKey is dropped *except* the ones
    named in `fk` (column name -> "table.column" target) -- this test only wants the
    one FK under investigation (scheduled_job_id) actually enforced; patrol_packs/
    scheduled_jobs also reference users/teams/skills/mcp_servers/inference_models/
    knowledge_bases, none of which are in scope here and would otherwise
    require seeding unrelated rows just to satisfy SQLite's checker.

    Non-primary-key columns are all made nullable here regardless of the source
    column's nullability: several real columns (created_at/updated_at/enabled/...)
    rely on Postgres-only `server_default` SQL (e.g. `CURRENT_TIMESTAMP(0)`) that
    SQLite's DDL parser rejects and that update_from_domain()/from_domain()
    deliberately never populate from the app side (see ScheduledJobModel.
    update_from_domain()'s `exclude={"created_at", ...}`). Their actual values are
    irrelevant to the insert-ordering/FK behavior under test here.
    """
    fk = fk or {}
    columns = []
    for c in orm_cls.__table__.columns:
        constraints = [ForeignKey(fk[c.name])] if c.name in fk else []
        columns.append(
            Column(
                c.name, c.type, *constraints, primary_key=c.primary_key, nullable=not c.primary_key
            )
        )
    return Table(orm_cls.__table__.name, metadata, *columns)


@pytest.fixture
def db():
    metadata = MetaData()
    _shadow_table(ScheduledJobModel, metadata)
    _shadow_table(PatrolPackModel, metadata, fk={"scheduled_job_id": "scheduled_jobs.id"})
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    metadata.create_all(engine)
    # autoflush=False matches api/app/infrastructure/storage/postgres.py's real
    # async_sessionmaker(..., autoflush=False) exactly. This is load-bearing: with
    # the (SQLAlchemy) default autoflush=True, DBPatrolRepository.save_pack()'s own
    # `db_session.get(PatrolPackModel, pack.id)` call auto-flushes the still-pending
    # ScheduledJobModel *for you* before the SELECT runs, which would silently paper
    # over the exact bug this file exists to catch.
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        adapter = _AsyncSessionAdapter(session)
        yield SimpleNamespace(
            session=session,
            scheduled_job=DBScheduledJobRepository(adapter),
            patrol=DBPatrolRepository(adapter),
        )
        session.rollback()
    engine.dispose()


def _demo_pack(job_id: str) -> PatrolPack:
    config = load_patrol_template("kubernetes-baseline-v1")
    return PatrolPack(
        owner_user_id="user-1",
        name="Demo",
        slug="demo",
        config=config,
        mcp_server_id="server-1",
        scheduled_job_id=job_id,
    )


@pytest.mark.asyncio
async def test_scheduled_job_is_flushed_before_dependent_patrol_pack(db):
    """The exact sequence PatrolPackService.create_pack() runs: save the
    ScheduledJob the Pack is about to reference, then save the Pack. Must not
    raise, and the persisted row must actually carry the FK'd id."""
    job = ScheduledJob(name="demo", owner_user_id="user-1")
    pack = _demo_pack(job.id)

    await db.scheduled_job.save(job)
    try:
        await db.patrol.save_pack(pack)
    except IntegrityError as exc:
        pytest.fail(
            "save_pack() raised a real FK violation -- scheduled_job.save() did not "
            f"flush before the dependent patrol_pack insert: {exc}"
        )

    db.session.commit()
    stored = db.session.get(PatrolPackModel, pack.id)
    assert stored is not None
    assert stored.scheduled_job_id == job.id
    stored_job = db.session.get(ScheduledJobModel, job.id)
    assert stored_job is not None


@pytest.mark.asyncio
async def test_scheduled_job_save_flushes_immediately(db):
    """Narrower, mechanism-level assertion (belt-and-suspenders alongside the
    end-to-end test above): after save(), the new ScheduledJob must not still
    be sitting in the session's pending-write set -- it must already be
    flushed to the database, which is what makes it visible to a later
    statement (patrol_packs' FK-checked INSERT) within the same transaction."""
    job = ScheduledJob(name="demo", owner_user_id="user-1")

    await db.scheduled_job.save(job)

    assert not db.session.new, "ScheduledJobModel is still pending (unflushed) after save()"
    assert db.session.get(ScheduledJobModel, job.id) is not None
