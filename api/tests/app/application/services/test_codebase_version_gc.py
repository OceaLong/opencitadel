#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Application, scheduler, and repository contracts for codebase version GC."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import (
    Column,
    DefaultClause,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.application.services.resource_version_gc_service import (
    ResourceVersionGCService,
)
from app.domain.models.app_config import CodebaseConfig
from app.domain.models.codebase import (
    ArtifactFormat,
    ArtifactKind,
    CodebaseSourceType,
    CodebaseStatus,
    EdgeKind,
    SymbolKind,
)
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.repositories.codebase_version_repository import (
    CodebaseVersionGCResult,
)
from app.infrastructure.external.scheduler.job_scheduler import (
    run_codebase_version_gc_tick,
    run_scheduler_loop,
)
from app.infrastructure.models.codebase import (
    CodebaseArtifactModel,
    CodebaseChunkModel,
    CodebaseEdgeModel,
    CodebaseFileModel,
    CodebaseModel,
    CodebaseSymbolModel,
)
from app.infrastructure.models.codebase_version import CodebaseVersionORM
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
    SessionResourceBindingORM,
)
from app.infrastructure.repositories.db_codebase_version_repository import (
    DBCodebaseVersionRepository,
)


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _GCRepository:
    def __init__(self, results: list[CodebaseVersionGCResult]) -> None:
        self._results = list(results)
        self.calls: list[dict] = []

    async def collect_garbage(self, **kwargs) -> CodebaseVersionGCResult:
        self.calls.append(kwargs)
        if self._results:
            return self._results.pop(0)
        return CodebaseVersionGCResult()


class _Uow:
    def __init__(self, repository: _GCRepository) -> None:
        self.codebase_version = repository
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1


class _SnapshotStorage:
    def __init__(self, keys: set[str]) -> None:
        self.keys = set(keys)
        self.deleted: list[str] = []

    async def delete_bytes(self, key: str) -> None:
        self.deleted.append(key)
        self.keys.discard(key)

    async def exists(self, key: str) -> bool:
        return key in self.keys


def _result(*version_ids: str, snapshot_keys=()) -> CodebaseVersionGCResult:
    return CodebaseVersionGCResult(
        collected_version_ids=tuple(version_ids),
        deleted_versions=len(version_ids),
        deleted_files=2,
        deleted_symbols=3,
        deleted_edges=4,
        deleted_chunks=5,
        deleted_artifacts=6,
        reclaimed_logical_bytes=17,
        deleted_builds=1,
        deleted_build_events=2,
        retained_shared_snapshots=1,
        protected_active_versions=1,
        protected_bound_versions=2,
        protected_active_build_versions=1,
        snapshot_keys_to_delete=tuple(snapshot_keys),
    )


@pytest.mark.asyncio
async def test_collect_codebase_forwards_cutoff_and_deletes_last_snapshot_ref():
    repository = _GCRepository([
        _result("expired", snapshot_keys=("snap-expired",)),
    ])
    uow = _Uow(repository)
    storage = _SnapshotStorage({"snap-expired", "snap-shared"})
    service = ResourceVersionGCService(
        uow_factory=lambda: uow,
        clock=lambda: NOW,
        object_storage=storage,
    )

    result = await service.collect_codebase_versions(
        retain_count=0,
        min_age_days=0,
        batch_size=25,
    )

    assert result.collected_version_ids == ("expired",)
    assert result.deleted_snapshots == 1
    assert result.retained_reference_count == 4
    assert not await storage.exists("snap-expired")
    assert await storage.exists("snap-shared")
    assert repository.calls == [
        {
            "retain_count": 0,
            "older_than": NOW,
            "batch_size": 25,
        }
    ]
    assert uow.entered == uow.exited == 1


@pytest.mark.asyncio
async def test_collect_codebase_uses_created_at_age_cutoff_and_is_idempotent():
    repository = _GCRepository([
        _result("expired", snapshot_keys=("snap-expired",)),
        CodebaseVersionGCResult(),
    ])
    storage = _SnapshotStorage({"snap-expired"})
    service = ResourceVersionGCService(
        uow_factory=lambda: _Uow(repository),
        clock=lambda: NOW,
        object_storage=storage,
    )

    first = await service.collect_codebase_versions(2, 30, 50)
    second = await service.collect_codebase_versions(2, 30, 50)

    assert first.collected_version_ids == ("expired",)
    assert second.collected_version_ids == ()
    assert first.deleted_snapshots == 1
    assert second.deleted_snapshots == 0
    assert repository.calls[0]["older_than"] == NOW - timedelta(days=30)
    assert repository.calls[1]["older_than"] == NOW - timedelta(days=30)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retain_count", "min_age_days", "batch_size"),
    (
        (-1, 0, 1),
        (0, -1, 1),
        (0, 0, 0),
        (0, 0, 501),
    ),
)
async def test_collect_codebase_rejects_invalid_policy_without_uow(
    retain_count,
    min_age_days,
    batch_size,
):
    factory = AsyncMock()
    service = ResourceVersionGCService(
        uow_factory=factory,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError):
        await service.collect_codebase_versions(
            retain_count,
            min_age_days,
            batch_size,
        )

    factory.assert_not_called()


def test_codebase_gc_result_is_frozen_and_metrics_are_deterministic():
    result = _result("b", "a", snapshot_keys=("snap-a", "snap-b"))

    with pytest.raises((AttributeError, TypeError)):
        result.deleted_versions = 99
    assert result.metrics() == {
        "collected_versions": 2,
        "deleted_artifacts": 6,
        "deleted_build_events": 2,
        "deleted_builds": 1,
        "deleted_chunks": 5,
        "deleted_edges": 4,
        "deleted_files": 2,
        "deleted_snapshots": 0,
        "deleted_symbols": 3,
        "deleted_versions": 2,
        "protected_active_build_versions": 1,
        "protected_active_versions": 1,
        "protected_age_versions": 0,
        "protected_bound_versions": 2,
        "protected_retention_versions": 0,
        "reclaimed_logical_bytes": 17,
        "retained_reference_count": 4,
        "retained_shared_snapshots": 1,
    }
    assert "snap-a" not in repr(result.metrics())


def test_codebase_gc_result_rejects_negative_counts():
    with pytest.raises(
        ValueError,
        match="codebase-version GC counters must be non-negative",
    ):
        CodebaseVersionGCResult(reclaimed_logical_bytes=-1)


def test_codebase_gc_config_is_default_off_and_policy_bounds_are_validated():
    config = CodebaseConfig()

    assert config.version_gc_enabled is False
    assert config.version_retention_count == 10
    assert config.version_retention_min_days == 30
    assert config.version_gc_batch_size == 50
    assert CodebaseConfig(
        version_retention_count=0,
        version_retention_min_days=0,
        version_gc_batch_size=1,
    ).version_retention_count == 0
    for payload in (
        {"version_retention_count": -1},
        {"version_retention_min_days": -1},
        {"version_gc_batch_size": 0},
        {"version_gc_batch_size": 501},
    ):
        with pytest.raises(ValidationError):
            CodebaseConfig(**payload)


def _runtime_config(*, scheduler_enabled: bool, codebase_gc_enabled: bool):
    return SimpleNamespace(
        scheduler=SimpleNamespace(
            enabled=scheduler_enabled,
            poll_interval_seconds=0.01,
            leader_lease_seconds=30,
            max_concurrent_jobs=5,
        ),
        knowledge_base=SimpleNamespace(
            version_gc_enabled=False,
            version_retention_count=2,
            version_retention_min_days=30,
            version_gc_batch_size=50,
        ),
        codebase=SimpleNamespace(
            version_gc_enabled=codebase_gc_enabled,
            version_retention_count=2,
            version_retention_min_days=30,
            version_gc_batch_size=50,
        ),
    )


class _SchedulerUow:
    def __init__(self) -> None:
        self.scheduled_job = SimpleNamespace(list_due=AsyncMock(return_value=[]))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scheduler_enabled", "codebase_gc_enabled", "expected_gc_calls"),
    (
        (False, True, 0),
        (True, False, 0),
        (True, True, 1),
    ),
)
async def test_scheduler_respects_global_and_codebase_gc_disable_gates(
    scheduler_enabled,
    codebase_gc_enabled,
    expected_gc_calls,
):
    stop = asyncio.Event()
    gc_service = SimpleNamespace(
        collect_knowledge_versions=AsyncMock(),
        collect_codebase_versions=AsyncMock(return_value=_result("old")),
    )
    leader = AsyncMock(return_value=True)

    async def run_gc_tick(service, **_kwargs):
        return await service.collect_codebase_versions(2, 30, 50)

    gc_tick = AsyncMock(side_effect=run_gc_tick)

    async def stop_after_tick(_seconds):
        stop.set()

    with (
        patch(
            "app.infrastructure.external.scheduler.job_scheduler."
            "get_runtime_config",
            return_value=_runtime_config(
                scheduler_enabled=scheduler_enabled,
                codebase_gc_enabled=codebase_gc_enabled,
            ),
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler."
            "try_become_scheduler_leader",
            leader,
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler.asyncio.sleep",
            side_effect=stop_after_tick,
        ),
        patch(
            "app.infrastructure.external.scheduler.job_scheduler."
            "run_codebase_version_gc_tick",
            gc_tick,
        ),
    ):
        await run_scheduler_loop(
            lambda: _SchedulerUow(),
            SimpleNamespace(),
            resource_version_gc_service=gc_service,
            stop_event=stop,
        )

    assert gc_service.collect_codebase_versions.await_count == expected_gc_calls
    assert gc_tick.await_count == expected_gc_calls
    if not scheduler_enabled:
        leader.assert_not_awaited()


class _AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.locked_statements = 0

    @property
    def bind(self):
        return self._session.bind

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement, params=None):
        if getattr(statement, "_for_update_arg", None) is not None:
            self.locked_statements += 1
        if params is None:
            return self._session.execute(statement)
        return self._session.execute(statement, params)

    async def flush(self) -> None:
        self._session.flush()


@pytest.fixture
def codebase_gc_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'codebase-gc.db'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for source in (
        ResourceBuildORM.__table__,
        ResourceBuildEventORM.__table__,
        CodebaseModel.__table__,
        CodebaseVersionORM.__table__,
        CodebaseFileModel.__table__,
        CodebaseSymbolModel.__table__,
        CodebaseEdgeModel.__table__,
        CodebaseChunkModel.__table__,
        CodebaseArtifactModel.__table__,
        SessionResourceBindingORM.__table__,
    ):
        source.to_metadata(metadata)
    for table_name in ("users", "teams", "sessions"):
        if table_name not in metadata.tables:
            Table(
                table_name,
                metadata,
                Column("id", String(255), primary_key=True),
            )
    for table in metadata.tables.values():
        for column in table.c:
            if column.server_default is not None:
                raw = str(column.server_default.arg)
                raw = raw.replace("::jsonb", "")
                raw = raw.replace("CURRENT_TIMESTAMP(0)", "CURRENT_TIMESTAMP")
                column.server_default = DefaultClause(text(raw))
    metadata.create_all(engine)

    with Session(engine, expire_on_commit=False) as session:
        adapter = _AsyncSessionAdapter(session)
        yield SimpleNamespace(
            repo=DBCodebaseVersionRepository(adapter),
            adapter=adapter,
            session=session,
        )
        session.rollback()
    engine.dispose()


def _seed_codebase(gc_db, codebase_id: str):
    gc_db.session.add(
        CodebaseModel(
            id=codebase_id,
            name=codebase_id,
            source_type=CodebaseSourceType.FILES.value,
            source_ref="{}",
            status=CodebaseStatus.READY.value,
        )
    )


def _seed_version(
    gc_db,
    version_id: str,
    *,
    codebase_id: str = "cb-1",
    age_days: int,
    source_snapshot_key: str | None = None,
):
    gc_db.session.add(
        CodebaseVersionORM.from_domain(
            CodebaseVersion(
                id=version_id,
                codebase_id=codebase_id,
                state=CodebaseVersionState.READY,
                source_snapshot_key=source_snapshot_key,
                source_revision=version_id,
                source_digest=f"{version_id:0<64}"[:64],
                created_at=NOW - timedelta(days=age_days),
                published_at=NOW - timedelta(days=age_days),
            )
        )
    )


def _seed_build(
    gc_db,
    *,
    build_id: str,
    codebase_id: str,
    version_id: str,
    state: BuildState,
):
    gc_db.session.add(
        ResourceBuildORM.from_domain(
            ResourceBuild(
                id=build_id,
                resource_kind=ResourceKind.CODEBASE,
                resource_id=codebase_id,
                version_id=version_id,
                command_key=f"build:{version_id}",
                state=state,
                created_by="test",
                created_at=NOW,
            )
        )
    )


def _seed_binding(
    gc_db,
    *,
    binding_id: str,
    session_id: str,
    codebase_id: str,
    version_id: str,
    is_current: bool,
):
    if gc_db.session.execute(
        text("SELECT id FROM sessions WHERE id = :id"),
        {"id": session_id},
    ).first() is None:
        gc_db.session.execute(
            text("INSERT INTO sessions (id) VALUES (:id)"),
            {"id": session_id},
        )
    binding = SessionResourceBinding(
        id=binding_id,
        session_id=session_id,
        resource_kind=ResourceKind.CODEBASE,
        resource_id=codebase_id,
        version_id=version_id,
        is_current=is_current,
        bound_by="test",
        created_at=NOW,
    )
    gc_db.session.add(SessionResourceBindingORM.from_domain(binding))


def _count(gc_db, model) -> int:
    return gc_db.session.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_repository_retains_active_bound_active_build_age_and_rank(
    codebase_gc_db,
):
    _seed_codebase(codebase_gc_db, "cb-1")
    for version_id, age in (
        ("expired", 100),
        ("historically-bound", 90),
        ("active", 80),
        ("building", 70),
        ("rank-kept-a", 60),
        ("rank-kept-b", 50),
        ("too-recent", 5),
    ):
        _seed_version(codebase_gc_db, version_id, age_days=age)
    codebase_gc_db.session.flush()
    codebase_gc_db.session.get(CodebaseModel, "cb-1").active_version_id = "active"
    _seed_binding(
        codebase_gc_db,
        binding_id="binding-history",
        session_id="session-old",
        codebase_id="cb-1",
        version_id="historically-bound",
        is_current=False,
    )
    _seed_build(
        codebase_gc_db,
        build_id="build-running",
        codebase_id="cb-1",
        version_id="building",
        state=BuildState.RUNNING,
    )
    codebase_gc_db.session.commit()

    result = await codebase_gc_db.repo.collect_garbage(
        retain_count=3,
        older_than=NOW - timedelta(days=30),
        batch_size=50,
    )
    codebase_gc_db.session.commit()

    assert result.collected_version_ids == ("expired",)
    assert result.protected_active_versions == 1
    assert result.protected_bound_versions == 1
    assert result.protected_active_build_versions == 1
    assert result.protected_age_versions == 1
    assert result.protected_retention_versions == 3
    surviving = codebase_gc_db.session.scalars(
        select(CodebaseVersionORM.id).order_by(CodebaseVersionORM.id)
    ).all()
    assert surviving == [
        "active",
        "building",
        "historically-bound",
        "rank-kept-a",
        "rank-kept-b",
        "too-recent",
    ]


@pytest.mark.asyncio
async def test_repository_deletes_analysis_closure_and_refcounts_snapshots(
    codebase_gc_db,
):
    _seed_codebase(codebase_gc_db, "cb-1")
    _seed_version(
        codebase_gc_db,
        "v-old",
        age_days=100,
        source_snapshot_key="snap-shared",
    )
    _seed_version(
        codebase_gc_db,
        "v-active",
        age_days=90,
        source_snapshot_key="snap-shared",
    )
    codebase_gc_db.session.flush()
    codebase_gc_db.session.get(CodebaseModel, "cb-1").active_version_id = "v-active"
    codebase_gc_db.session.add(
        CodebaseFileModel(
            id="file-old",
            codebase_id="cb-1",
            version_id="v-old",
            path="src/main.py",
            language="python",
        )
    )
    codebase_gc_db.session.add(
        CodebaseSymbolModel(
            id="symbol-old",
            codebase_id="cb-1",
            version_id="v-old",
            file_id="file-old",
            name="main",
            qualified_name="src.main.main",
            kind=SymbolKind.FUNCTION.value,
        )
    )
    codebase_gc_db.session.flush()
    codebase_gc_db.session.add(
        CodebaseEdgeModel(
            id="edge-old",
            codebase_id="cb-1",
            version_id="v-old",
            src_symbol_id="symbol-old",
            dst_symbol_id="symbol-old",
            callee_name="main",
            kind=EdgeKind.CALL.value,
        )
    )
    codebase_gc_db.session.add(
        CodebaseChunkModel(
            id="chunk-old",
            codebase_id="cb-1",
            version_id="v-old",
            file_id="file-old",
            symbol_id="symbol-old",
            content="old-代码",
            search_text="old code",
        )
    )
    codebase_gc_db.session.add(
        CodebaseArtifactModel(
            id="artifact-old",
            codebase_id="cb-1",
            version_id="v-old",
            kind=ArtifactKind.CALL_CHAIN.value,
            format=ArtifactFormat.MERMAID.value,
            title="old",
            content="graph LR",
        )
    )
    _seed_build(
        codebase_gc_db,
        build_id="build-old",
        codebase_id="cb-1",
        version_id="v-old",
        state=BuildState.SUCCEEDED,
    )
    codebase_gc_db.session.flush()
    codebase_gc_db.session.add(
        ResourceBuildEventORM(
            id="event-old",
            build_id="build-old",
            seq=1,
            state=BuildState.SUCCEEDED.value,
        )
    )
    codebase_gc_db.session.commit()

    result = await codebase_gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=50,
    )
    codebase_gc_db.session.commit()

    assert result.collected_version_ids == ("v-old",)
    assert result.deleted_versions == 1
    assert result.deleted_files == 1
    assert result.deleted_symbols == 1
    assert result.deleted_edges == 1
    assert result.deleted_chunks == 1
    assert result.deleted_artifacts == 1
    assert result.reclaimed_logical_bytes == len("old-代码".encode("utf-8"))
    assert result.deleted_build_events == 1
    assert result.deleted_builds == 1
    assert result.retained_shared_snapshots == 1
    assert result.snapshot_keys_to_delete == ()
    assert codebase_gc_db.session.get(CodebaseVersionORM, "v-active") is not None
    assert _count(codebase_gc_db, CodebaseFileModel) == 0

    codebase_gc_db.session.get(CodebaseModel, "cb-1").active_version_id = None
    codebase_gc_db.session.commit()
    final = await codebase_gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=50,
    )
    codebase_gc_db.session.commit()

    assert final.collected_version_ids == ("v-active",)
    assert final.retained_shared_snapshots == 0
    assert final.snapshot_keys_to_delete == ("snap-shared",)
    assert codebase_gc_db.session.get(CodebaseVersionORM, "v-active") is None


@pytest.mark.asyncio
async def test_repository_batch_is_global_deterministic_and_repeat_idempotent(
    codebase_gc_db,
):
    _seed_codebase(codebase_gc_db, "cb-b")
    _seed_codebase(codebase_gc_db, "cb-a")
    _seed_version(codebase_gc_db, "v-b-old", codebase_id="cb-b", age_days=100)
    _seed_version(codebase_gc_db, "v-a-old", codebase_id="cb-a", age_days=100)
    _seed_version(codebase_gc_db, "v-a-newer", codebase_id="cb-a", age_days=90)
    codebase_gc_db.session.commit()

    first = await codebase_gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=2,
    )
    codebase_gc_db.session.commit()
    second = await codebase_gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=2,
    )
    codebase_gc_db.session.commit()
    third = await codebase_gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=2,
    )
    codebase_gc_db.session.commit()

    assert first.collected_version_ids == ("v-a-old", "v-b-old")
    assert second.collected_version_ids == ("v-a-newer",)
    assert third.collected_version_ids == ()
    assert first.deleted_versions == 2
    assert second.deleted_versions == 1
    assert third.deleted_versions == 0
    assert third.reclaimed_logical_bytes == 0
    assert _count(codebase_gc_db, CodebaseVersionORM) == 0
    assert codebase_gc_db.adapter.locked_statements >= 5
