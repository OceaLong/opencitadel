#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Focused deletion-order and reference-closure tests for KB version GC."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
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

from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
    SessionResourceBinding,
)
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeChunkModel,
    KnowledgeDocumentModel,
    KnowledgeEntityModel,
    KnowledgeEntityRefModel,
    KnowledgeRelationModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
    KnowledgeDocumentRevisionORM,
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.models.resource_governance import (
    ResourceBuildEventORM,
    ResourceBuildORM,
    SessionResourceBindingORM,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)


NOW = datetime(2026, 7, 30, 4, 0, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


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
def gc_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'knowledge-gc.db'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for source in (
        KnowledgeBaseModel.__table__,
        KnowledgeDocumentModel.__table__,
        ResourceBuildORM.__table__,
        ResourceBuildEventORM.__table__,
        KnowledgeBaseVersionORM.__table__,
        KnowledgeDocumentRevisionORM.__table__,
        KnowledgeVersionDocumentORM.__table__,
        KnowledgeChunkModel.__table__,
        KnowledgeEntityModel.__table__,
        KnowledgeRelationModel.__table__,
        KnowledgeEntityRefModel.__table__,
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
            repo=DBKnowledgeVersionRepository(adapter),
            adapter=adapter,
            session=session,
            metadata=metadata,
        )
        session.rollback()
    engine.dispose()


def _seed_kb(gc_db, kb_id: str, *, active_version_id: str | None = None):
    gc_db.session.add(
        KnowledgeBaseModel(
            id=kb_id,
            name=kb_id,
            active_version_id=active_version_id,
        )
    )


def _seed_version(
    gc_db,
    version_id: str,
    *,
    kb_id: str = "kb-1",
    age_days: int,
    parent_version_id: str | None = None,
):
    gc_db.session.add(
        KnowledgeBaseVersionORM.from_domain(
            KnowledgeBaseVersion(
                id=version_id,
                knowledge_base_id=kb_id,
                parent_version_id=parent_version_id,
                state=KnowledgeVersionState.READY,
                created_at=NOW - timedelta(days=age_days),
                published_at=NOW - timedelta(days=age_days),
            )
        )
    )


def _seed_build(
    gc_db,
    *,
    build_id: str,
    kb_id: str,
    version_id: str,
    state: BuildState,
):
    gc_db.session.add(
        ResourceBuildORM.from_domain(
            ResourceBuild(
                id=build_id,
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id=kb_id,
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
    kb_id: str,
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
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id=kb_id,
        version_id=version_id,
        is_current=is_current,
        bound_by="test",
        created_at=NOW,
    )
    gc_db.session.add(SessionResourceBindingORM.from_domain(binding))


def _count(gc_db, model) -> int:
    return gc_db.session.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_gc_retains_active_historical_binding_active_build_age_and_rank(
    gc_db,
):
    _seed_kb(gc_db, "kb-1")
    for version_id, age in (
        ("expired", 100),
        ("historically-bound", 90),
        ("active", 80),
        ("building", 70),
        ("rank-kept-a", 60),
        ("rank-kept-b", 50),
        ("too-recent", 5),
    ):
        _seed_version(gc_db, version_id, age_days=age)
    gc_db.session.flush()
    gc_db.session.get(KnowledgeBaseModel, "kb-1").active_version_id = "active"
    _seed_binding(
        gc_db,
        binding_id="binding-history",
        session_id="session-old",
        kb_id="kb-1",
        version_id="historically-bound",
        is_current=False,
    )
    # Deliberately do not set KnowledgeBaseVersionORM.build_id. The exact
    # resource target is authoritative, not that denormalized pointer.
    _seed_build(
        gc_db,
        build_id="build-running",
        kb_id="kb-1",
        version_id="building",
        state=BuildState.RUNNING,
    )
    gc_db.session.commit()

    result = await gc_db.repo.collect_garbage(
        retain_count=3,
        older_than=NOW - timedelta(days=30),
        batch_size=50,
    )
    gc_db.session.commit()

    assert result.collected_version_ids == ("expired",)
    assert result.protected_active_versions == 1
    assert result.protected_bound_versions == 1
    assert result.protected_active_build_versions == 1
    assert result.protected_age_versions == 1
    assert result.protected_retention_versions == 3
    surviving = gc_db.session.scalars(
        select(KnowledgeBaseVersionORM.id).order_by(
            KnowledgeBaseVersionORM.id
        )
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
async def test_gc_deletes_dependency_closure_but_keeps_shared_revision_and_doc(
    gc_db,
):
    _seed_kb(gc_db, "kb-1")
    _seed_version(gc_db, "v-old", age_days=100)
    _seed_version(
        gc_db,
        "v-active",
        age_days=90,
    )
    gc_db.session.flush()
    gc_db.session.get(KnowledgeBaseModel, "kb-1").active_version_id = "v-active"
    gc_db.session.add(
        KnowledgeDocumentModel(
            id="doc-1",
            kb_id="kb-1",
            title="logical source",
            source_ref="s3://must-survive",
        )
    )
    gc_db.session.add(
        KnowledgeDocumentRevisionORM(
            id="rev-shared",
            document_id="doc-1",
            source_ref="s3://must-survive",
            source_digest="a" * 64,
            state="indexed",
        )
    )
    for version_id in ("v-old", "v-active"):
        gc_db.session.add(
            KnowledgeVersionDocumentORM(
                version_id=version_id,
                knowledge_base_id="kb-1",
                document_id="doc-1",
                document_revision_id="rev-shared",
                ordinal=0,
                state="indexed",
            )
        )
    gc_db.session.flush()
    gc_db.session.add(
        KnowledgeChunkModel(
            id="chunk-old",
            kb_id="kb-1",
            doc_id="doc-1",
            version_id="v-old",
            content="old-知识",
        )
    )
    gc_db.session.add(
        KnowledgeEntityModel(
            id="entity-old",
            kb_id="kb-1",
            version_id="v-old",
            name="old",
            normalized_name="old",
        )
    )
    gc_db.session.flush()
    gc_db.session.add(
        KnowledgeRelationModel(
            id="relation-old",
            kb_id="kb-1",
            version_id="v-old",
            src_entity_id="entity-old",
            dst_entity_id="entity-old",
            relation="self",
            chunk_id="chunk-old",
        )
    )
    gc_db.session.add(
        KnowledgeEntityRefModel(
            id="ref-old",
            kb_id="kb-1",
            version_id="v-old",
            entity_id="entity-old",
            doc_id="doc-1",
        )
    )
    _seed_build(
        gc_db,
        build_id="build-old",
        kb_id="kb-1",
        version_id="v-old",
        state=BuildState.SUCCEEDED,
    )
    gc_db.session.flush()
    gc_db.session.add(
        ResourceBuildEventORM(
            id="event-old",
            build_id="build-old",
            seq=1,
            state=BuildState.SUCCEEDED.value,
        )
    )
    gc_db.session.commit()

    result = await gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=50,
    )
    gc_db.session.commit()

    assert result.collected_version_ids == ("v-old",)
    assert result.deleted_versions == 1
    assert result.deleted_relations == 1
    assert result.deleted_entity_refs == 1
    assert result.deleted_chunks == 1
    assert result.reclaimed_logical_bytes == len(
        "old-知识".encode("utf-8")
    )
    assert result.deleted_entities == 1
    assert result.deleted_manifests == 1
    assert result.deleted_revisions == 0
    assert result.retained_shared_revisions == 1
    assert result.deleted_build_events == 1
    assert result.deleted_builds == 1
    assert gc_db.session.get(KnowledgeDocumentModel, "doc-1") is not None
    assert (
        gc_db.session.get(KnowledgeDocumentRevisionORM, "rev-shared")
        is not None
    )
    # The PG migration clears only the direct pointer. SQLite omits this
    # PG-specific FK, so the repository must never depend on rewriting it.
    assert (
        gc_db.session.get(KnowledgeBaseVersionORM, "v-active")
        is not None
    )

    gc_db.session.get(
        KnowledgeBaseModel,
        "kb-1",
    ).active_version_id = None
    gc_db.session.commit()
    final = await gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=50,
    )
    gc_db.session.commit()

    assert final.collected_version_ids == ("v-active",)
    assert final.deleted_revisions == 1
    assert final.retained_shared_revisions == 0
    assert final.reclaimed_logical_bytes == 0
    assert gc_db.session.get(
        KnowledgeDocumentRevisionORM,
        "rev-shared",
    ) is None
    assert gc_db.session.get(KnowledgeDocumentModel, "doc-1") is not None


@pytest.mark.asyncio
async def test_gc_batch_is_global_deterministic_and_repeat_idempotent(gc_db):
    _seed_kb(gc_db, "kb-b")
    _seed_kb(gc_db, "kb-a")
    _seed_version(gc_db, "v-b-old", kb_id="kb-b", age_days=100)
    _seed_version(gc_db, "v-a-old", kb_id="kb-a", age_days=100)
    _seed_version(gc_db, "v-a-newer", kb_id="kb-a", age_days=90)
    gc_db.session.commit()

    first = await gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=2,
    )
    gc_db.session.commit()
    second = await gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=2,
    )
    gc_db.session.commit()
    third = await gc_db.repo.collect_garbage(
        retain_count=0,
        older_than=NOW,
        batch_size=2,
    )
    gc_db.session.commit()

    assert first.collected_version_ids == ("v-a-old", "v-b-old")
    assert second.collected_version_ids == ("v-a-newer",)
    assert third.collected_version_ids == ()
    assert first.deleted_versions == 2
    assert second.deleted_versions == 1
    assert third.deleted_versions == 0
    assert third.reclaimed_logical_bytes == 0
    assert _count(gc_db, KnowledgeBaseVersionORM) == 0
    assert gc_db.adapter.locked_statements >= 5
