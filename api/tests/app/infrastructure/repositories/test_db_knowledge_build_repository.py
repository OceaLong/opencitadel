#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Repository seams used by copy-on-write KB candidate construction."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import Column, DefaultClause, MetaData, String, Table, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.domain.models.knowledge_base import KnowledgeBase, KnowledgeDocument
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
)
from app.domain.models.resource_governance import ResourceBuild, ResourceKind
from app.infrastructure.models.knowledge_base import (
    KnowledgeBaseModel,
    KnowledgeDocumentModel,
)
from app.infrastructure.models.knowledge_version import (
    KnowledgeBaseVersionORM,
    KnowledgeDocumentRevisionORM,
    KnowledgeVersionDocumentORM,
)
from app.infrastructure.models.resource_governance import ResourceBuildORM
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_resource_governance_repository import (
    DBResourceGovernanceRepository,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


class _Adapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, value):
        self.session.add(value)

    def add_all(self, values):
        self.session.add_all(values)

    async def execute(self, statement):
        return self.session.execute(statement)

    async def flush(self):
        self.session.flush()


@pytest.mark.asyncio
async def test_revision_manifest_and_active_build_seams_are_owner_closed(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'builder-repo.db'}")
    metadata = MetaData()
    for source in (
        KnowledgeBaseModel.__table__,
        KnowledgeDocumentModel.__table__,
        ResourceBuildORM.__table__,
        KnowledgeBaseVersionORM.__table__,
        KnowledgeDocumentRevisionORM.__table__,
        KnowledgeVersionDocumentORM.__table__,
    ):
        source.to_metadata(metadata)
    for name in ("users", "teams"):
        if name not in metadata.tables:
            Table(name, metadata, Column("id", String(255), primary_key=True))
    for table in metadata.tables.values():
        for column in table.c:
            if column.server_default is not None:
                raw = str(column.server_default.arg)
                raw = raw.replace("::jsonb", "")
                raw = raw.replace(
                    "CURRENT_TIMESTAMP(0)",
                    "CURRENT_TIMESTAMP",
                )
                column.server_default = DefaultClause(text(raw))
    metadata.create_all(engine)
    now = datetime(2026, 7, 29, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        adapter = _Adapter(session)
        kb_repo = DBKnowledgeBaseRepository(adapter)
        version_repo = DBKnowledgeVersionRepository(adapter)
        build_repo = DBResourceGovernanceRepository(adapter)
        await kb_repo.save_kb(KnowledgeBase(id="kb1", name="KB"))
        await kb_repo.insert_document(
            KnowledgeDocument(id="d1", kb_id="kb1", title="doc")
        )
        build = ResourceBuild(
            id="b1",
            resource_kind=ResourceKind.KNOWLEDGE_BASE,
            resource_id="kb1",
            version_id="v1",
            command_key="a" * 64,
            created_by="user1",
            created_at=now,
        )
        await build_repo.add_build(build)
        await version_repo.create_candidate(
            KnowledgeBaseVersion(
                id="v1",
                knowledge_base_id="kb1",
                build_id="b1",
                created_at=now,
            )
        )
        revision = KnowledgeDocumentRevision(
            id="r1",
            document_id="d1",
            source_digest="b" * 64,
            created_at=now,
        )
        await version_repo.add_revision(
            revision,
            knowledge_base_id="kb1",
        )
        await version_repo.add_manifest(
            "v1",
            [
                KnowledgeVersionDocument(
                    version_id="v1",
                    document_id="d1",
                    document_revision_id="r1",
                    ordinal=0,
                )
            ],
            knowledge_base_id="kb1",
        )
        session.commit()

        active = await build_repo.get_active_build(
            ResourceKind.KNOWLEDGE_BASE,
            "kb1",
        )
        assert active is not None and active.id == "b1"
        stored_revisions = await version_repo.get_revisions(
            ["r1"],
            knowledge_base_id="kb1",
        )
        assert set(stored_revisions) == {"r1"}
        assert stored_revisions["r1"].document_id == revision.document_id
        assert (
            stored_revisions["r1"].source_digest
            == revision.source_digest
        )
        historical = await version_repo.get_revision_by_digest(
            "d1",
            "b" * 64,
            knowledge_base_id="kb1",
        )
        assert historical is not None and historical.id == "r1"
        assert (
            await version_repo.get_revision_by_digest(
                "d1",
                "b" * 64,
                knowledge_base_id="foreign",
            )
            is None
        )
        assert [
            entry.document_revision_id
            for entry in await version_repo.get_manifest(
                "v1",
                knowledge_base_id="kb1",
            )
        ] == ["r1"]
        candidate = await version_repo.get_build_candidate("b1")
        assert candidate is not None
        assert candidate[0].id == "v1"
        assert [item.document_id for item in candidate[1]] == ["d1"]

        for state in (
            DocumentRevisionState.PARSING,
            DocumentRevisionState.PARSED,
            DocumentRevisionState.INDEXING,
            DocumentRevisionState.INDEXED,
        ):
            transitioned = await version_repo.transition_document(
                "v1",
                "d1",
                knowledge_base_id="kb1",
                state=state,
                parsed_blocks=(
                    [
                        {
                            "page_no": 1,
                            "heading_path": "doc",
                            "text": "known",
                        }
                    ]
                    if state is DocumentRevisionState.PARSED
                    else None
                ),
                page_count=(
                    1
                    if state is DocumentRevisionState.PARSED
                    else None
                ),
            )
            assert transitioned.state is state
        stored_after_transition = await version_repo.get_revisions(
            ["r1"],
            knowledge_base_id="kb1",
        )
        assert (
            stored_after_transition["r1"].state
            is DocumentRevisionState.INDEXED
        )
        assert stored_after_transition["r1"].parsed_blocks[0]["text"] == (
            "known"
        )

        stale = await build_repo.list_stale_builds(
            ResourceKind.KNOWLEDGE_BASE,
            stale_before=now.replace(year=2027),
        )
        assert [item.id for item in stale] == ["b1"]
        assert (
            await version_repo.get_revisions(
                ["r1"],
                knowledge_base_id="foreign",
            )
            == {}
        )
    engine.dispose()
