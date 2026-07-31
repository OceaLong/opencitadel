#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Persistence and compare-and-swap contracts for knowledge versions."""
import asyncio
import inspect
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    DefaultClause,
    MetaData,
    Table,
    create_engine,
    delete,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
)
from app.infrastructure.models.knowledge_base import KnowledgeBaseModel
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.models.resource_governance import ResourceBuildORM
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.db_authorization import (
    configure_session_authorization,
)
from core.config import get_settings


NOW = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)


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
def version_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge-versions.db'}")
    # Clone the full dependency closure once, then create only the needed tables.
    metadata = MetaData()
    for source in (
        KnowledgeBaseModel.__table__,
        ResourceBuildORM.__table__,
        KnowledgeBaseVersionORM.__table__,
        KnowledgeVersionDocumentORM.__table__,
    ):
        source.to_metadata(metadata)
    # Supply minimal external targets referenced by the cloned tables.
    from sqlalchemy import Column, String

    if "users" not in metadata.tables:
        Table("users", metadata, Column("id", String(255), primary_key=True))
    if "teams" not in metadata.tables:
        Table("teams", metadata, Column("id", String(255), primary_key=True))
    if "knowledge_documents" not in metadata.tables:
        Table(
            "knowledge_documents",
            metadata,
            Column("id", String(255), primary_key=True),
            Column("kb_id", String(255), nullable=False),
        )
    if "knowledge_document_revisions" not in metadata.tables:
        Table(
            "knowledge_document_revisions",
            metadata,
            Column("id", String(255), primary_key=True),
            Column("document_id", String(255), nullable=False),
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
            repo=DBKnowledgeVersionRepository(adapter),
            kb_repo=DBKnowledgeBaseRepository(adapter),
            adapter=adapter,
            session=session,
            engine=engine,
        )
        session.rollback()
    engine.dispose()


def _kb(active: str | None = None) -> KnowledgeBase:
    return KnowledgeBase(
        id="kb-1",
        name="KB",
        owner_user_id="user-1",
        active_version_id=active,
        created_at=NOW.replace(tzinfo=None),
        updated_at=NOW.replace(tzinfo=None),
    )


def _version(
    version_id: str,
    *,
    parent: str | None = None,
    build_id: str | None = None,
    offset: int = 0,
) -> KnowledgeBaseVersion:
    return KnowledgeBaseVersion(
        id=version_id,
        knowledge_base_id="kb-1",
        parent_version_id=parent,
        build_id=build_id,
        created_at=NOW + timedelta(minutes=offset),
    )


def _seed_build(
    session: Session,
    *,
    build_id: str,
    version_id: str,
    parent_version_id: str | None = None,
    resource_kind: ResourceKind = ResourceKind.KNOWLEDGE_BASE,
    resource_id: str = "kb-1",
    state: BuildState = BuildState.QUEUED,
) -> None:
    session.add(
        ResourceBuildORM.from_domain(
            ResourceBuild(
                id=build_id,
                resource_kind=resource_kind,
                resource_id=resource_id,
                version_id=version_id,
                parent_version_id=parent_version_id,
                command_key=f"build:{version_id}",
                state=state,
                created_by="user-1",
                created_at=NOW,
            )
        )
    )


def _seed_version(
    session: Session,
    version: KnowledgeBaseVersion,
) -> None:
    session.add(KnowledgeBaseVersionORM.from_domain(version))


@pytest.mark.asyncio
async def test_fix1_red_p1_1_all_version_operations_require_kb_closure(
    version_db,
):
    protocol_methods = (
        "get_version",
        "publish_candidate",
        "fail_candidate",
    )
    for method_name in protocol_methods:
        signature = inspect.signature(
            getattr(DBKnowledgeVersionRepository, method_name)
        )
        parameter = signature.parameters["knowledge_base_id"]
        assert parameter.default is inspect.Parameter.empty

    with pytest.raises(TypeError):
        await version_db.repo.get_version("globally-addressable-version")


@pytest.mark.asyncio
async def test_fix1_red_p1_2_candidate_requires_matching_active_build(
    version_db,
):
    await version_db.kb_repo.save_kb(_kb())
    version_db.session.commit()

    with pytest.raises(ValueError, match="build"):
        await version_db.repo.create_candidate(_version("orphan"))

    for state in (
        BuildState.SUCCEEDED,
        BuildState.DEGRADED,
        BuildState.FAILED,
        BuildState.CANCELLED,
    ):
        candidate_id = f"terminal-{state.value}"
        _seed_build(
            version_db.session,
            build_id=f"build-{candidate_id}",
            version_id=candidate_id,
            state=state,
        )
        version_db.session.commit()
        with pytest.raises(ValueError, match="build"):
            await version_db.repo.create_candidate(
                _version(candidate_id, build_id=f"build-{candidate_id}")
            )
        version_db.session.delete(
            version_db.session.get(
                ResourceBuildORM, f"build-{candidate_id}"
            )
        )
        version_db.session.commit()

    mismatch_cases = (
        (
            "missing",
            {},
        ),
        (
            "wrong-kind",
            {"resource_kind": ResourceKind.CODEBASE},
        ),
        (
            "wrong-kb",
            {"resource_id": "kb-foreign"},
        ),
        (
            "wrong-version",
            {"version_id": "another-version"},
        ),
        (
            "wrong-parent",
            {"parent_version_id": "another-parent"},
        ),
    )
    for candidate_id, overrides in mismatch_cases:
        build_id = f"build-{candidate_id}"
        if candidate_id != "missing":
            _seed_build(
                version_db.session,
                build_id=build_id,
                version_id=overrides.pop("version_id", candidate_id),
                **overrides,
            )
            version_db.session.commit()
        with pytest.raises(ValueError, match="build"):
            await version_db.repo.create_candidate(
                _version(candidate_id, build_id=build_id)
            )
        if candidate_id != "missing":
            version_db.session.delete(
                version_db.session.get(ResourceBuildORM, build_id)
            )
            version_db.session.commit()

    assert (
        await version_db.repo.list_versions("kb-1", limit=100)
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "reasons"),
    (
        (KnowledgeVersionState.READY, ["SHOULD_BE_EMPTY"]),
        (KnowledgeVersionState.DEGRADED, []),
    ),
)
async def test_fix1_red_p1_3_contradictory_publish_is_zero_effect(
    version_db,
    state,
    reasons,
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("candidate"))
    version_db.session.commit()
    candidate_before = await version_db.repo.get_version(
        "candidate", knowledge_base_id="kb-1"
    )
    kb_before = await version_db.kb_repo.get_kb("kb-1")
    locked_before = version_db.adapter.locked_statements

    with pytest.raises(ValueError, match="degrad"):
        await version_db.repo.publish_candidate(
            "candidate",
            knowledge_base_id="kb-1",
            expected_active_version_id=None,
            state=state,
            capabilities={"keyword_search": True},
            degraded_reasons=reasons,
            metrics={"documents": 1},
        )

    assert (
        await version_db.repo.get_version(
            "candidate", knowledge_base_id="kb-1"
        )
        == candidate_before
    )
    assert await version_db.kb_repo.get_kb("kb-1") == kb_before
    assert version_db.adapter.locked_statements == locked_before


@pytest.mark.asyncio
async def test_publish_and_fail_foreign_kb_closure_are_zero_effect(
    version_db,
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("candidate"))
    version_db.session.commit()
    before = await version_db.repo.get_version(
        "candidate", knowledge_base_id="kb-1"
    )

    assert not await version_db.repo.publish_candidate(
        "candidate",
        knowledge_base_id="kb-foreign",
        expected_active_version_id=None,
        state=KnowledgeVersionState.READY,
        capabilities={"keyword_search": True},
        degraded_reasons=[],
        metrics={},
    )
    assert not await version_db.repo.fail_candidate(
        "candidate",
        knowledge_base_id="kb-foreign",
        metrics={"failed": 1},
    )

    assert await version_db.repo.get_version(
        "candidate", knowledge_base_id="kb-1"
    ) == before
    assert (await version_db.kb_repo.get_kb("kb-1")).active_version_id is None


def test_fix1_red_p2_2_live_cas_test_proves_lock_waiting():
    source = inspect.getsource(
        test_postgres_same_and_competing_candidate_cas_races
    )

    assert "lock_acquired" in source
    assert "release_lock" in source
    assert "done() is False" in source


class _CommitFailAdapter(_AsyncSessionAdapter):
    async def execute(self, statement, params=None):
        if "set_config('app.auth_mode'" in str(statement):
            return SimpleNamespace()
        return await super().execute(statement)

    async def commit(self) -> None:
        raise RuntimeError("injected commit failure after publish flush")

    async def rollback(self) -> None:
        self._session.rollback()

    async def close(self) -> None:
        self._session.close()


@pytest.mark.asyncio
async def test_fix1_red_p2_3_commit_failure_rolls_back_flushed_publication(
    version_db,
):
    await version_db.kb_repo.save_kb(_kb(active="v1"))
    _seed_version(
        version_db.session,
        KnowledgeBaseVersion(
            id="v1",
            knowledge_base_id="kb-1",
            state=KnowledgeVersionState.READY,
            published_at=NOW,
            created_at=NOW,
        ),
    )
    _seed_version(
        version_db.session,
        _version("v2", parent="v1", offset=1),
    )
    version_db.session.commit()
    candidate_before = await version_db.repo.get_version(
        "v2", knowledge_base_id="kb-1"
    )

    failing_session = Session(
        version_db.engine,
        expire_on_commit=False,
    )
    adapter = _CommitFailAdapter(failing_session)
    uow = DBUnitOfWork(
        lambda: adapter,
        authorization_context=AuthorizationContext.system(
            "knowledge-version-commit-failure"
        ),
    )
    with pytest.raises(RuntimeError, match="injected commit failure"):
        async with uow as entered:
            assert await entered.knowledge_version.publish_candidate(
                "v2",
                knowledge_base_id="kb-1",
                expected_active_version_id="v1",
                state=KnowledgeVersionState.READY,
                capabilities={"keyword_search": True},
                degraded_reasons=[],
                metrics={"documents": 1},
            )

    with Session(version_db.engine, expire_on_commit=False) as verification:
        verify_repo = DBKnowledgeVersionRepository(
            _AsyncSessionAdapter(verification)
        )
        verify_kb = DBKnowledgeBaseRepository(
            _AsyncSessionAdapter(verification)
        )
        assert (
            await verify_repo.get_version(
                "v2", knowledge_base_id="kb-1"
            )
            == candidate_before
        )
        assert (await verify_kb.get_kb("kb-1")).active_version_id == "v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active_build_state",
    (BuildState.QUEUED, BuildState.RUNNING),
)
async def test_candidate_parent_and_build_must_close_over_same_kb(
    version_db,
    active_build_state,
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("v1"))
    _seed_build(
        version_db.session,
        build_id="build-v2",
        version_id="v2",
        parent_version_id="v1",
        state=active_build_state,
    )
    version_db.session.commit()

    created = await version_db.repo.create_candidate(
        _version("v2", parent="v1", build_id="build-v2")
    )
    assert created.id == "v2"
    assert (
        await version_db.repo.get_version(
            "v2", knowledge_base_id="kb-1"
        )
    ).parent_version_id == "v1"

    with pytest.raises(ValueError, match="parent"):
        await version_db.repo.create_candidate(
            _version("bad-parent", parent="missing")
        )
    with pytest.raises(ValueError, match="build"):
        await version_db.repo.create_candidate(
            _version("bad-build", build_id="build-v2")
        )
    assert (await version_db.kb_repo.get_kb("kb-1")).active_version_id is None


@pytest.mark.asyncio
async def test_round_trip_is_deeply_immutable_and_list_order_is_stable(version_db):
    await version_db.kb_repo.save_kb(_kb())
    first = _version("same-b", offset=0)
    second = _version("same-a", offset=0)
    _seed_version(version_db.session, first)
    _seed_version(version_db.session, second)
    version_db.session.commit()

    records = await version_db.repo.list_versions("kb-1", limit=10)

    assert [record.id for record in records] == ["same-b", "same-a"]
    with pytest.raises(TypeError):
        records[0].metrics["mutate"] = True
    assert await version_db.repo.get_version(
        "same-b", knowledge_base_id="other-kb"
    ) is None


@pytest.mark.asyncio
async def test_duplicate_ids_and_invalid_pagination_are_rejected(version_db):
    await version_db.kb_repo.save_kb(_kb())
    _seed_build(
        version_db.session,
        build_id="build-v1",
        version_id="v1",
    )
    await version_db.repo.create_candidate(
        _version("v1", build_id="build-v1")
    )
    version_db.session.commit()

    with pytest.raises(IntegrityError):
        await version_db.repo.create_candidate(
            _version("v1", build_id="build-v1")
        )
    version_db.session.rollback()

    for limit, before in (
        (0, None),
        (501, None),
        (10, (NOW, "")),
        (10, ("not-a-datetime", "v1")),
    ):
        with pytest.raises(ValueError):
            await version_db.repo.list_versions(
                "kb-1", limit=limit, before=before
            )
    assert (
        await version_db.repo.get_version(
            "v1", knowledge_base_id="kb-1"
        )
    ).state is KnowledgeVersionState.BUILDING


@pytest.mark.asyncio
async def test_manifest_is_same_kb_closed_and_ordered_by_ordinal(version_db):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("v1"))
    version_db.session.execute(
        text(
            "INSERT INTO knowledge_documents (id, kb_id) "
            "VALUES ('doc-1', 'kb-1'), ('doc-2', 'kb-1')"
        )
    )
    version_db.session.execute(
        text(
            "INSERT INTO knowledge_document_revisions (id, document_id) "
            "VALUES ('rev-1', 'doc-1'), ('rev-2', 'doc-2')"
        )
    )
    for item in (
        KnowledgeVersionDocument(
            version_id="v1",
            document_id="doc-2",
            document_revision_id="rev-2",
            ordinal=2,
            state=DocumentRevisionState.INDEXED,
        ),
        KnowledgeVersionDocument(
            version_id="v1",
            document_id="doc-1",
            document_revision_id="rev-1",
            ordinal=1,
            state=DocumentRevisionState.PARSED,
        ),
    ):
        version_db.session.add(
            KnowledgeVersionDocumentORM.from_domain(
                item, knowledge_base_id="kb-1"
            )
        )
    version_db.session.commit()

    manifest = await version_db.repo.get_manifest(
        "v1", knowledge_base_id="kb-1"
    )
    assert [item.document_id for item in manifest] == ["doc-1", "doc-2"]
    assert (
        await version_db.repo.get_manifest(
            "v1", knowledge_base_id="other-kb"
        )
        == []
    )


@pytest.mark.asyncio
async def test_publish_is_locked_atomic_cas_and_loser_is_completely_untouched(
    version_db,
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("v1"))
    assert await version_db.repo.publish_candidate(
        "v1",
        knowledge_base_id="kb-1",
        expected_active_version_id=None,
        state=KnowledgeVersionState.READY,
        capabilities={"keyword_search": True, "vector_search": False},
        degraded_reasons=[],
        metrics={"documents": 3},
    )
    version_db.session.commit()

    _seed_version(version_db.session, _version("v2", parent="v1"))
    _seed_version(version_db.session, _version("v3", parent="v1"))
    version_db.session.commit()

    assert await version_db.repo.publish_candidate(
        "v2",
        knowledge_base_id="kb-1",
        expected_active_version_id="v1",
        state=KnowledgeVersionState.DEGRADED,
        capabilities={"keyword_search": True, "vector_search": False},
        degraded_reasons=["EMBEDDING_UNAVAILABLE"],
        metrics={"documents": 4},
    )
    loser_before = await version_db.repo.get_version(
        "v3", knowledge_base_id="kb-1"
    )
    assert not await version_db.repo.publish_candidate(
        "v3",
        knowledge_base_id="kb-1",
        expected_active_version_id="v1",
        state=KnowledgeVersionState.READY,
        capabilities={"should_not": True},
        degraded_reasons=[],
        metrics={"documents": 999},
    )
    repeated_before = await version_db.repo.get_version(
        "v2", knowledge_base_id="kb-1"
    )
    assert not await version_db.repo.publish_candidate(
        "v2",
        knowledge_base_id="kb-1",
        expected_active_version_id="v1",
        state=KnowledgeVersionState.READY,
        capabilities={"mutated": True},
        degraded_reasons=[],
        metrics={"documents": 1000},
    )

    assert await version_db.repo.get_version(
        "v3", knowledge_base_id="kb-1"
    ) == loser_before
    assert await version_db.repo.get_version(
        "v2", knowledge_base_id="kb-1"
    ) == repeated_before
    active = await version_db.kb_repo.get_kb("kb-1")
    assert active.active_version_id == "v2"
    assert repeated_before.state is KnowledgeVersionState.DEGRADED
    assert repeated_before.degraded_reasons == ("EMBEDDING_UNAVAILABLE",)
    assert version_db.adapter.locked_statements >= 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_state",
    (KnowledgeVersionState.BUILDING, KnowledgeVersionState.FAILED),
)
async def test_publish_rejects_nonpublished_target_states(
    version_db, invalid_state
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("v1"))
    with pytest.raises(ValueError, match="ready or degraded"):
        await version_db.repo.publish_candidate(
            "v1",
            knowledge_base_id="kb-1",
            expected_active_version_id=None,
            state=invalid_state,
            capabilities={},
            degraded_reasons=[],
            metrics={},
        )


@pytest.mark.asyncio
async def test_failed_candidate_never_changes_active_and_cannot_publish(
    version_db,
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("v1"))
    await version_db.repo.publish_candidate(
        "v1",
        knowledge_base_id="kb-1",
        expected_active_version_id=None,
        state=KnowledgeVersionState.READY,
        capabilities={},
        degraded_reasons=[],
        metrics={},
    )
    _seed_version(version_db.session, _version("v2", parent="v1"))
    assert await version_db.repo.fail_candidate(
        "v2",
        knowledge_base_id="kb-1",
        metrics={"documents": 1, "failed": 1},
    )
    failed_before = await version_db.repo.get_version(
        "v2", knowledge_base_id="kb-1"
    )
    assert not await version_db.repo.fail_candidate(
        "v2",
        knowledge_base_id="kb-1",
        metrics={"changed": 1},
    )
    assert not await version_db.repo.publish_candidate(
        "v2",
        knowledge_base_id="kb-1",
        expected_active_version_id="v1",
        state=KnowledgeVersionState.READY,
        capabilities={},
        degraded_reasons=[],
        metrics={},
    )
    assert await version_db.repo.get_version(
        "v2", knowledge_base_id="kb-1"
    ) == failed_before
    assert (await version_db.kb_repo.get_kb("kb-1")).active_version_id == "v1"


@pytest.mark.asyncio
async def test_transaction_rollback_restores_candidate_and_active_together(
    version_db,
):
    await version_db.kb_repo.save_kb(_kb())
    _seed_version(version_db.session, _version("v1"))
    version_db.session.commit()

    assert await version_db.repo.publish_candidate(
        "v1",
        knowledge_base_id="kb-1",
        expected_active_version_id=None,
        state=KnowledgeVersionState.READY,
        capabilities={"keyword_search": True},
        degraded_reasons=[],
        metrics={"documents": 1},
    )
    version_db.session.rollback()

    assert (
        await version_db.repo.get_version(
            "v1", knowledge_base_id="kb-1"
        )
    ).state is KnowledgeVersionState.BUILDING
    assert (await version_db.kb_repo.get_kb("kb-1")).active_version_id is None


@pytest.mark.skipif(
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires opt-in live PostgreSQL row-lock verification",
)
@pytest.mark.asyncio
@pytest.mark.parametrize("same_candidate", (True, False))
async def test_postgres_same_and_competing_candidate_cas_races(
    _db_schema,
    same_candidate,
):
    """Live proof: a locked KB row admits exactly one CAS winner."""
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    kb_id = f"kb-version-cas-{suffix}"
    v1_id = f"{kb_id}-v1"
    v2_id = f"{kb_id}-v2"
    v3_id = v2_id if same_candidate else f"{kb_id}-v3"
    system = AuthorizationContext.system("knowledge-version-cas-test")
    candidate_before: dict[str, KnowledgeBaseVersion] = {}
    blocker_task = None
    left_task = None
    right_task = None

    async def configured_session():
        session = session_factory()
        await configure_session_authorization(session, system)
        return session

    try:
        setup = await configured_session()
        try:
            setup.add(
                KnowledgeBaseModel(
                    id=kb_id,
                    name="CAS test",
                    status="ready",
                )
            )
            setup.add(
                KnowledgeBaseVersionORM.from_domain(
                    KnowledgeBaseVersion(
                        id=v1_id,
                        knowledge_base_id=kb_id,
                        state=KnowledgeVersionState.READY,
                        capabilities={"keyword_search": True},
                        published_at=NOW,
                        created_at=NOW,
                    )
                )
            )
            await setup.flush()
            kb = await setup.get(KnowledgeBaseModel, kb_id)
            kb.active_version_id = v1_id
            for candidate_id in {v2_id, v3_id}:
                candidate = KnowledgeBaseVersion(
                    id=candidate_id,
                    knowledge_base_id=kb_id,
                    parent_version_id=v1_id,
                    created_at=NOW + timedelta(minutes=1),
                )
                candidate_before[candidate_id] = candidate
                setup.add(
                    KnowledgeBaseVersionORM.from_domain(
                        candidate
                    )
                )
            await setup.commit()
        finally:
            await setup.close()

        lock_acquired = asyncio.Event()
        release_lock = asyncio.Event()
        publisher_gate = asyncio.Event()
        left_ready = asyncio.Event()
        right_ready = asyncio.Event()

        async def hold_kb_lock() -> None:
            session = await configured_session()
            try:
                locked = (
                    await session.execute(
                        select(KnowledgeBaseModel)
                        .where(KnowledgeBaseModel.id == kb_id)
                        .with_for_update()
                    )
                ).scalar_one()
                assert locked.id == kb_id
                lock_acquired.set()
                await release_lock.wait()
                await session.commit()
            finally:
                await session.close()

        async def publish(
            candidate_id: str,
            ready: asyncio.Event,
        ) -> bool:
            session = await configured_session()
            try:
                ready.set()
                await publisher_gate.wait()
                result = await DBKnowledgeVersionRepository(
                    session
                ).publish_candidate(
                    candidate_id,
                    knowledge_base_id=kb_id,
                    expected_active_version_id=v1_id,
                    state=KnowledgeVersionState.READY,
                    capabilities={"keyword_search": True},
                    degraded_reasons=[],
                    metrics={"documents": 1},
                )
                await session.commit()
                return result
            finally:
                await session.close()

        blocker_task = asyncio.create_task(hold_kb_lock())
        await asyncio.wait_for(lock_acquired.wait(), timeout=5)
        left_task = asyncio.create_task(publish(v2_id, left_ready))
        right_task = asyncio.create_task(publish(v3_id, right_ready))
        await asyncio.wait_for(
            asyncio.gather(left_ready.wait(), right_ready.wait()),
            timeout=5,
        )
        publisher_gate.set()
        await asyncio.sleep(0.1)
        assert left_task.done() is False
        assert right_task.done() is False

        release_lock.set()
        await asyncio.wait_for(blocker_task, timeout=5)
        results = await asyncio.wait_for(
            asyncio.gather(left_task, right_task),
            timeout=5,
        )
        assert sorted(results) == [False, True]

        verification = await configured_session()
        try:
            kb = await verification.get(KnowledgeBaseModel, kb_id)
            candidate_rows = (
                await verification.execute(
                    select(KnowledgeBaseVersionORM)
                    .where(
                        KnowledgeBaseVersionORM.knowledge_base_id == kb_id,
                        KnowledgeBaseVersionORM.id != v1_id,
                    )
                    .order_by(KnowledgeBaseVersionORM.id)
                )
            ).scalars().all()
            candidates = {
                row.id: row.to_domain()
                for row in candidate_rows
            }
            assert kb.active_version_id in {v2_id, v3_id}
            assert sum(
                row.state is KnowledgeVersionState.READY
                for row in candidates.values()
            ) == 1
            assert sum(
                row.published_at is not None
                for row in candidates.values()
            ) == 1
            if not same_candidate:
                loser_id = (
                    v3_id if kb.active_version_id == v2_id else v2_id
                )
                assert candidates[loser_id] == candidate_before[loser_id]
        finally:
            await verification.close()
    finally:
        pending = [
            task
            for task in (left_task, right_task, blocker_task)
            if task is not None and not task.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        cleanup = await configured_session()
        try:
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == kb_id
                )
            )
            await cleanup.commit()
        finally:
            await cleanup.close()
            await engine.dispose()


@pytest.mark.skipif(
    os.getenv("OPENCITADEL_RUN_POSTGRES_INTEGRATION") != "1",
    reason="requires opt-in live PostgreSQL deferred-commit verification",
)
@pytest.mark.asyncio
async def test_postgres_uow_commit_failure_rolls_back_publication(
    _db_schema,
):
    """A deferred FK failure after publish flush rolls back both CAS rows."""
    engine = create_async_engine(get_settings().sqlalchemy_database_uri)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    kb_id = f"kb-version-commit-failure-{suffix}"
    v1_id = f"{kb_id}-v1"
    v2_id = f"{kb_id}-v2"
    fault_id = f"{kb_id}-fault"
    system = AuthorizationContext.system(
        "knowledge-version-postgres-commit-failure"
    )
    candidate_before = KnowledgeBaseVersion(
        id=v2_id,
        knowledge_base_id=kb_id,
        parent_version_id=v1_id,
        created_at=NOW + timedelta(minutes=1),
    )
    try:
        async with session_factory() as setup:
            await configure_session_authorization(setup, system)
            setup.add(
                KnowledgeBaseModel(
                    id=kb_id,
                    name="Commit failure test",
                    status="ready",
                )
            )
            setup.add(
                KnowledgeBaseVersionORM.from_domain(
                    KnowledgeBaseVersion(
                        id=v1_id,
                        knowledge_base_id=kb_id,
                        state=KnowledgeVersionState.READY,
                        published_at=NOW,
                        created_at=NOW,
                    )
                )
            )
            setup.add(
                KnowledgeBaseVersionORM.from_domain(candidate_before)
            )
            await setup.flush()
            kb = await setup.get(KnowledgeBaseModel, kb_id)
            kb.active_version_id = v1_id
            await setup.commit()

        with pytest.raises(IntegrityError):
            async with DBUnitOfWork(
                session_factory,
                authorization_context=system,
            ) as uow:
                assert await uow.knowledge_version.publish_candidate(
                    v2_id,
                    knowledge_base_id=kb_id,
                    expected_active_version_id=v1_id,
                    state=KnowledgeVersionState.READY,
                    capabilities={"keyword_search": True},
                    degraded_reasons=[],
                    metrics={"documents": 1},
                )
                await uow.db_session.execute(
                    text(
                        "INSERT INTO knowledge_base_versions "
                        "(id, knowledge_base_id, parent_version_id) "
                        "VALUES (:id, :kb_id, :missing_parent)"
                    ),
                    {
                        "id": fault_id,
                        "kb_id": kb_id,
                        "missing_parent": f"{kb_id}-missing",
                    },
                )

        async with session_factory() as verification:
            await configure_session_authorization(verification, system)
            kb = await verification.get(KnowledgeBaseModel, kb_id)
            candidate = await verification.get(
                KnowledgeBaseVersionORM, v2_id
            )
            fault = await verification.get(
                KnowledgeBaseVersionORM, fault_id
            )
            assert kb.active_version_id == v1_id
            assert candidate.to_domain() == candidate_before
            assert fault is None
    finally:
        async with session_factory() as cleanup:
            await configure_session_authorization(cleanup, system)
            await cleanup.execute(
                delete(KnowledgeBaseModel).where(
                    KnowledgeBaseModel.id == kb_id
                )
            )
            await cleanup.commit()
        await engine.dispose()
