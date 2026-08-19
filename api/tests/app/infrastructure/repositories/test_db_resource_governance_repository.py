#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    DefaultClause,
    MetaData,
    create_engine,
    event as sqlalchemy_event,
    select,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceBuildEvent,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.scope import OwnerScope
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
    SessionResourceBindingORM,
)
from app.domain.models.session import Session as DomainSession
from app.infrastructure.repositories.db_resource_governance_repository import (
    DBResourceGovernanceRepository,
)
from app.infrastructure.repositories.db_session_repository import (
    DBSessionRepository,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.locked_statements = 0

    def add(self, instance) -> None:
        self._session.add(instance)

    async def execute(self, statement):
        if getattr(statement, "_for_update_arg", None) is not None:
            self.locked_statements += 1
        return self._session.execute(statement)

    async def flush(self) -> None:
        self._session.flush()


@pytest.fixture
def binding_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resource-bindings.db'}")
    SessionResourceBindingORM.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        adapter = _AsyncSessionAdapter(session)
        yield SimpleNamespace(
            repo=DBResourceGovernanceRepository(adapter),
            adapter=adapter,
            session=session,
        )
        session.rollback()
    engine.dispose()


@pytest.fixture
def build_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resource-builds.db'}")
    metadata = MetaData()
    build_table = ResourceBuildORM.__table__.to_metadata(metadata)
    event_table = ResourceBuildEventORM.__table__.to_metadata(metadata)
    for name, default in (
        ("capabilities", "'[]'"),
        ("degraded_reasons", "'[]'"),
        ("metrics", "'{}'"),
    ):
        build_table.c[name].server_default = DefaultClause(text(default))
    event_table.c.payload.server_default = DefaultClause(text("'{}'"))
    metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        adapter = _AsyncSessionAdapter(session)
        yield SimpleNamespace(
            repo=DBResourceGovernanceRepository(adapter),
            adapter=adapter,
            session=session,
            engine=engine,
        )
        session.rollback()
    engine.dispose()


def _build() -> ResourceBuild:
    return ResourceBuild(
        id="build-1",
        resource_kind=ResourceKind.CODEBASE,
        resource_id="cb1",
        version_id="cbv2",
        parent_version_id="cbv1",
        command_key="reanalyze:cb1",
        created_by="u1",
        created_at=datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc),
    )


def _build_event(
    *,
    event_id: str,
    state: BuildState,
    phase: str,
    progress: float,
    payload: dict | None = None,
) -> ResourceBuildEvent:
    return ResourceBuildEvent(
        id=event_id,
        build_id="build-1",
        seq=0,
        state=state,
        phase=phase,
        progress=progress,
        payload=payload or {},
        created_at=datetime(2026, 7, 29, 1, 1, tzinfo=timezone.utc),
    )


def _binding(
    *,
    binding_id: str,
    version_id: str,
    current: bool = True,
    supersedes: str | None = None,
    created_at: datetime | None = None,
) -> SessionResourceBinding:
    return SessionResourceBinding(
        id=binding_id,
        session_id="s1",
        resource_kind=ResourceKind.CODEBASE,
        resource_id="cb1",
        version_id=version_id,
        is_current=current,
        supersedes_binding_id=supersedes,
        bound_by="u1",
        created_at=created_at
        or datetime(2026, 7, 29, 1, 2, 3, tzinfo=timezone.utc),
    )


def test_resource_orms_map_every_persisted_field_exactly():
    build = ResourceBuild(
        id="build-1",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id="kbv2",
        parent_version_id="kbv1",
        command_key="reindex:kb1",
        state=BuildState.DEGRADED,
        phase="publish",
        progress=1.0,
        capabilities=["keyword_search"],
        degraded_reasons=["EMBEDDING_UNAVAILABLE"],
        metrics={"documents": 7, "score": 0.5},
        error_code=None,
        error_message=None,
        heartbeat_at=datetime(2026, 7, 29, 1, 3, tzinfo=timezone.utc),
        last_event_seq=4,
        created_by="u1",
        created_at=datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc),
        started_at=datetime(2026, 7, 29, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 29, 1, 4, tzinfo=timezone.utc),
    )
    build_event = ResourceBuildEvent(
        id="event-1",
        build_id="build-1",
        seq=4,
        phase="publish",
        state=BuildState.DEGRADED,
        progress=1.0,
        payload={"degraded_reasons": ["EMBEDDING_UNAVAILABLE"]},
        created_at=datetime(2026, 7, 29, 1, 4, tzinfo=timezone.utc),
    )
    binding = _binding(
        binding_id="binding-1",
        version_id="cbv1",
    )

    mapped_build = ResourceBuildORM.from_domain(build).to_domain()
    mapped_event = ResourceBuildEventORM.from_domain(
        build_event
    ).to_domain()
    mapped_binding = SessionResourceBindingORM.from_domain(
        binding
    ).to_domain()

    assert mapped_build == build
    assert mapped_event == build_event
    assert mapped_binding == binding


@pytest.mark.asyncio
async def test_build_repository_locks_allocates_and_replays_in_sequence(
    build_db,
):
    await build_db.repo.add_build(_build())
    build_db.session.commit()

    first = await build_db.repo.append_event(
        "build-1",
        _build_event(
            event_id="event-1",
            state=BuildState.RUNNING,
            phase="parse",
            progress=0.25,
        ),
    )
    second = await build_db.repo.append_event(
        "build-1",
        _build_event(
            event_id="event-2",
            state=BuildState.RUNNING,
            phase="index",
            progress=0.75,
        ),
    )
    replay = await build_db.repo.list_events(
        "build-1",
        after_seq=1,
        limit=10,
    )
    stored_build = await build_db.repo.get_build("build-1")

    assert (first, second) == (1, 2)
    assert [event.seq for event in replay] == [2]
    assert replay[0].phase == "index"
    assert stored_build is not None
    assert stored_build.state == BuildState.RUNNING
    assert stored_build.phase == "index"
    assert stored_build.progress == 0.75
    assert stored_build.last_event_seq == 2
    assert stored_build.heartbeat_at == replay[0].created_at
    assert stored_build.started_at is not None
    assert build_db.adapter.locked_statements == 2


@pytest.mark.asyncio
async def test_build_repository_terminal_retry_is_semantically_idempotent(
    build_db,
):
    await build_db.repo.add_build(_build())
    await build_db.repo.append_event(
        "build-1",
        _build_event(
            event_id="event-running",
            state=BuildState.RUNNING,
            phase="work",
            progress=0.5,
        ),
    )
    terminal = _build_event(
        event_id="event-terminal",
        state=BuildState.SUCCEEDED,
        phase="publish",
        progress=1.0,
        payload={
            "version_id": "cbv2",
            "capabilities": {
                "keyword_search": True,
                "vector_search": False,
            },
            "degraded_reasons": [],
            "metrics": {"child_chunk_count": 7},
        },
    )
    assert await build_db.repo.append_event("build-1", terminal) == 2

    duplicate = terminal.model_copy(update={"id": "retry-id", "seq": 0})
    assert await build_db.repo.append_event("build-1", duplicate) == 2
    with pytest.raises(ValueError, match="terminal"):
        await build_db.repo.append_event(
            "build-1",
            terminal.model_copy(
                update={"id": "conflict-id", "payload": {"different": True}}
            ),
        )

    assert [
        event.seq
        for event in await build_db.repo.list_events(
            "build-1", after_seq=0, limit=10
        )
    ] == [1, 2]
    stored = await build_db.repo.get_build("build-1")
    assert stored.finished_at is not None
    assert stored.capabilities == ["keyword_search"]
    assert stored.degraded_reasons == []
    assert stored.metrics == {"child_chunk_count": 7}


@pytest.mark.asyncio
async def test_running_graph_checkpoint_metrics_survive_reload(build_db):
    await build_db.repo.add_build(_build())
    await build_db.repo.append_event(
        "build-1",
        _build_event(
            event_id="event-graph-checkpoint",
            state=BuildState.RUNNING,
            phase="graph",
            progress=0.7,
            payload={
                "metrics": {
                    "graph_cursor": "cursor-v1",
                    "graph_processed_count": 4,
                    "graph_llm_call_count": 4,
                    "graph_token_count": 22,
                }
            },
        ),
    )
    build_db.session.commit()

    reloaded = await build_db.repo.get_build("build-1")
    assert reloaded is not None
    assert reloaded.state is BuildState.RUNNING
    assert reloaded.metrics == {
        "graph_cursor": "cursor-v1",
        "graph_processed_count": 4,
        "graph_llm_call_count": 4,
        "graph_token_count": 22,
    }


@pytest.mark.asyncio
async def test_build_repository_rejects_unbounded_replay_queries(build_db):
    for after_seq, limit in ((-1, 10), (0, 0), (0, 501)):
        with pytest.raises(ValueError):
            await build_db.repo.list_events(
                "build-1",
                after_seq=after_seq,
                limit=limit,
            )


@pytest.mark.asyncio
async def test_build_event_insert_failure_rolls_back_cursor_and_heartbeat(
    build_db,
):
    await build_db.repo.add_build(_build())
    build_db.session.commit()

    def fail_event_flush(_session, _flush_context, _instances):
        if any(
            isinstance(record, ResourceBuildEventORM)
            for record in _session.new
        ):
            raise RuntimeError("injected event insert failure")

    sqlalchemy_event.listen(
        build_db.session,
        "before_flush",
        fail_event_flush,
    )
    try:
        with pytest.raises(RuntimeError, match="injected"):
            await build_db.repo.append_event(
                "build-1",
                _build_event(
                    event_id="event-fails",
                    state=BuildState.RUNNING,
                    phase="parse",
                    progress=0.25,
                ),
            )
        build_db.session.rollback()
    finally:
        sqlalchemy_event.remove(
            build_db.session,
            "before_flush",
            fail_event_flush,
        )

    with Session(build_db.engine, expire_on_commit=False) as verification:
        build = verification.get(ResourceBuildORM, "build-1")
        events = verification.execute(
            select(ResourceBuildEventORM)
        ).scalars().all()
        assert build.last_event_seq == 0
        assert build.heartbeat_at is None
        assert build.state == BuildState.QUEUED.value
        assert events == []


@pytest.mark.asyncio
async def test_binding_repository_round_trips_current_and_history(binding_db):
    old = _binding(binding_id="binding-1", version_id="cbv1")
    await binding_db.repo.add_binding(old)
    loaded = await binding_db.repo.get_current_binding(
        "s1",
        ResourceKind.CODEBASE,
    )

    replacement = _binding(
        binding_id="binding-2",
        version_id="cbv2",
        supersedes=old.id,
        created_at=datetime(2026, 7, 29, 1, 3, tzinfo=timezone.utc),
    )
    await binding_db.repo.replace_current_binding(loaded, replacement)
    current = await binding_db.repo.get_current_binding(
        "s1",
        ResourceKind.CODEBASE,
    )
    history = await binding_db.repo.list_bindings(
        "s1",
        ResourceKind.CODEBASE,
    )

    assert current == replacement
    assert [item.id for item in history] == ["binding-1", "binding-2"]
    assert [item.is_current for item in history] == [False, True]
    assert history[1].supersedes_binding_id == history[0].id


@pytest.mark.asyncio
async def test_get_current_binding_uses_row_lock_when_requested(binding_db):
    await binding_db.repo.add_binding(
        _binding(binding_id="binding-1", version_id="cbv1")
    )

    loaded = await binding_db.repo.get_current_binding(
        "s1",
        ResourceKind.CODEBASE,
        for_update=True,
    )

    assert loaded is not None
    assert binding_db.adapter.locked_statements == 1


@pytest.mark.asyncio
async def test_list_current_bindings_has_stable_kind_order(binding_db):
    await binding_db.repo.add_binding(
        _binding(binding_id="binding-code", version_id="cbv1")
    )
    await binding_db.repo.add_binding(
        SessionResourceBinding(
            id="binding-kb",
            session_id="s1",
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id="kb1",
            version_id="kbv1",
            bound_by="u1",
            created_at=datetime(
                2026,
                7,
                29,
                1,
                4,
                tzinfo=timezone.utc,
            ),
        )
    )

    current = await binding_db.repo.list_current_bindings("s1")

    assert [item.resource_kind for item in current] == [
        ResourceKind.CODEBASE,
        ResourceKind.KNOWLEDGE_BASE,
    ]


@pytest.mark.asyncio
async def test_session_metadata_projects_current_binding_ids_and_versions():
    binding_record = SessionResourceBindingORM.from_domain(
        _binding(binding_id="binding-code", version_id="cbv1")
    )
    record = SimpleNamespace(
        id="s1",
        to_domain=lambda: DomainSession(
            id="s1",
            owner_user_id="u1",
        )
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return self

        def all(self):
            return self.value

    class _Adapter:
        def __init__(self):
            self.results = [_Result(record), _Result([binding_record])]

        async def execute(self, _statement):
            return self.results.pop(0)

    loaded = await DBSessionRepository(_Adapter()).get_metadata("s1")

    assert loaded is not None
    assert [item.model_dump(mode="json") for item in loaded.resource_bindings] == [
        {
            "binding_id": "binding-code",
            "resource_kind": "codebase",
            "resource_id": "cb1",
            "version_id": "cbv1",
        }
    ]


@pytest.mark.asyncio
async def test_new_session_is_flushed_before_repository_returns():
    class _Result:
        @staticmethod
        def scalar_one_or_none():
            return None

    class _Adapter:
        def __init__(self):
            self.added = []
            self.flush_calls = 0

        async def execute(self, _statement):
            return _Result()

        def add(self, record):
            self.added.append(record)

        async def flush(self):
            self.flush_calls += 1

    adapter = _Adapter()

    await DBSessionRepository(adapter).save(
        DomainSession(id="new-session", owner_user_id="u1")
    )

    assert [record.id for record in adapter.added] == ["new-session"]
    assert adapter.flush_calls == 1


@pytest.mark.asyncio
async def test_session_lock_is_scope_filtered_and_uses_for_update():
    record = SimpleNamespace(
        to_domain=lambda: DomainSession(
            id="s1",
            owner_user_id="u1",
        )
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

        def scalars(self):
            return SimpleNamespace(all=lambda: [])

    class _Adapter:
        def __init__(self):
            self.results = [_Result(None), _Result(record), _Result(None)]
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    adapter = _Adapter()
    sessions = DBSessionRepository(adapter)
    denied = await sessions.lock_by_id(
        "s1",
        scope=OwnerScope.personal("intruder"),
    )
    allowed = await sessions.lock_by_id(
        "s1",
        scope=OwnerScope.personal("u1"),
    )

    assert denied is None
    assert allowed is not None
    assert all(
        getattr(statement, "_for_update_arg", None) is not None
        for statement in adapter.statements[:2]
    )
    assert getattr(adapter.statements[2], "_for_update_arg", None) is None
    assert all(
        "sessions.owner_user_id" in str(statement)
        for statement in adapter.statements[:2]
    )


@pytest.mark.asyncio
async def test_failed_upgrade_transaction_can_restore_previous_current(
    binding_db,
):
    old = _binding(binding_id="binding-1", version_id="cbv1")
    await binding_db.repo.add_binding(old)
    binding_db.session.commit()
    loaded = await binding_db.repo.get_current_binding(
        "s1",
        ResourceKind.CODEBASE,
        for_update=True,
    )
    replacement = _binding(
        binding_id="binding-2",
        version_id="cbv2",
        supersedes=old.id,
    )

    await binding_db.repo.replace_current_binding(loaded, replacement)
    binding_db.session.rollback()

    stored = binding_db.session.execute(
        select(SessionResourceBindingORM).order_by(
            SessionResourceBindingORM.created_at
        )
    ).scalars().all()
    assert len(stored) == 1
    assert stored[0].id == old.id
    assert stored[0].is_current is True


def test_current_binding_index_is_partial_unique_in_postgresql_metadata():
    index = next(
        index
        for index in SessionResourceBindingORM.__table__.indexes
        if index.name == "uq_session_resource_bindings_current"
    )
    sql = str(
        index.compile(dialect=postgresql.dialect())
        if hasattr(index, "compile")
        else ""
    )
    if not sql:
        from sqlalchemy.schema import CreateIndex

        sql = str(
            CreateIndex(index).compile(dialect=postgresql.dialect())
        )

    assert index.unique is True
    assert "(session_id, resource_kind)" in sql
    assert "WHERE is_current = true" in sql
