"""Build real text chunks and publish capabilities under the admitted policy."""

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.models.file import File
from app.domain.models.knowledge_base import (
    ChunkLevel,
    DocStatus,
    KBStatus,
    KnowledgeBase,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.repositories.patch import UNSET
from app.domain.runtime_policy import KnowledgeGraphRagPolicy, initial_execution_policy
from app.domain.services.knowledge_base.ingestion_runner import KBIngestionRunner
from tests.app.embedding_test_support import embedding_space


class _CandidateUow:
    """Only persistence is fake; parsing, chunking and publication are real."""

    def __init__(self):
        self.kb = KnowledgeBase(id="kb-1", name="Handbook", owner_user_id="user-1")
        self.document = KnowledgeDocument(
            id="doc-1",
            kb_id=self.kb.id,
            title="handbook.txt",
            source_ref='{"file_id":"file-1"}',
            file_id="file-1",
            mime="text/plain",
        )
        self.revision = KnowledgeDocumentRevision(
            id="revision-1",
            document_id=self.document.id,
            source_ref=self.document.source_ref,
            source_digest="a" * 64,
        )
        self.version = KnowledgeBaseVersion(
            id="version-1", knowledge_base_id=self.kb.id, build_id="build-1"
        )
        self.manifest = [
            KnowledgeVersionDocument(
                version_id=self.version.id,
                document_id=self.document.id,
                document_revision_id=self.revision.id,
                ordinal=0,
            )
        ]
        self.chunks = []
        self.knowledge_version = SimpleNamespace(
            get_build_candidate=AsyncMock(side_effect=lambda _: (self.version, self.manifest)),
            get_revisions=AsyncMock(
                side_effect=lambda *_a, **_k: {self.revision.id: self.revision}
            ),
            transition_document=AsyncMock(side_effect=self._transition),
            publish_candidate=AsyncMock(side_effect=self._publish),
            fail_candidate=AsyncMock(return_value=True),
        )
        self.knowledge_base = SimpleNamespace(
            get_kb=AsyncMock(side_effect=lambda _: self.kb),
            get_document_for_build=AsyncMock(return_value=self.document),
            replace_candidate_chunks=AsyncMock(side_effect=self._replace_chunks),
            get_candidate_index_metrics=AsyncMock(side_effect=self._metrics),
            update_document_status=AsyncMock(),
            save_kb=AsyncMock(),
        )
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def _transition(self, _version_id, _document_id, **kwargs):
        changes = {
            key: value
            for key, value in kwargs.items()
            if key != "knowledge_base_id" and value is not UNSET
        }
        self.revision = KnowledgeDocumentRevision.model_validate(
            {**self.revision.model_dump(), **changes}
        )
        return True

    async def _replace_chunks(self, _kb_id, _version_id, chunks):
        self.chunks = chunks

    async def _metrics(self, *_args):
        children = [chunk for chunk in self.chunks if chunk.level is ChunkLevel.CHILD]
        return {
            "document_count": 1,
            "indexed_document_count": int(self.revision.state is DocumentRevisionState.INDEXED),
            "failed_document_count": int(self.revision.state is DocumentRevisionState.FAILED),
            "parent_chunk_count": len(self.chunks) - len(children),
            "child_chunk_count": len(children),
            "vector_chunk_count": sum(bool(chunk.embedding) for chunk in children),
            "entity_count": 0,
            "relation_count": 0,
        }

    async def _publish(self, _version_id, **kwargs):
        self.version = KnowledgeBaseVersion.model_validate(
            {
                **self.version.model_dump(),
                **{
                    key: kwargs[key]
                    for key in ("state", "capabilities", "degraded_reasons", "metrics")
                },
                "published_at": datetime.now(UTC),
            }
        )
        self.kb.active_version_id = self.version.id
        return True


@pytest.mark.asyncio
@pytest.mark.parametrize("enhancement", [None, "vector", "graph"])
async def test_text_build_publishes_basic_ready_and_only_requested_degradation(enhancement):
    uow = _CandidateUow()
    policy = initial_execution_policy().knowledge_base
    if enhancement == "vector":
        policy = policy.model_copy(update={"vector_enabled": True})
        space = embedding_space()
        uow.version = uow.version.model_copy(
            update={"embedding_space": space, "embedding_space_digest": space.digest}
        )
    elif enhancement == "graph":
        policy = policy.model_copy(update={"graphrag": KnowledgeGraphRagPolicy(enabled=True)})
    contents = b"Production releases require approval. The handbook documents the approval process."
    storage = Mock()
    storage.download_file = AsyncMock(
        side_effect=lambda _: (
            BytesIO(contents),
            File(id="file-1", filename="handbook.txt", mime_type="text/plain", size=len(contents)),
        )
    )
    embeddings = Mock()
    embeddings.embed = AsyncMock(side_effect=RuntimeError("embedding provider unavailable"))
    ocr_llm = Mock(invoke=AsyncMock(side_effect=AssertionError("OCR is disabled")))
    graph_llm = (
        None
        if enhancement == "graph"
        else Mock(invoke=AsyncMock(side_effect=AssertionError("GraphRAG is disabled")))
    )
    runner = KBIngestionRunner(
        uow_factory=lambda: uow,
        file_storage=storage,
        web_documents=Mock(),
        embeddings=embeddings,
    )

    events = [
        event
        async for event in runner.run_build(
            "build-1",
            policy=policy,
            embedding_model_id="model-1" if enhancement == "vector" else None,
            embedding_dimensions=1536 if enhancement == "vector" else None,
            embedding_space_digest=embedding_space().digest if enhancement == "vector" else None,
            graph_llm=graph_llm,
            ocr_llm=ocr_llm,
        )
    ]

    assert events[-1].kind == "done"
    assert not [event for event in events if event.kind == "error"]
    assert uow.revision.state is DocumentRevisionState.INDEXED
    assert uow.revision.parsed_blocks
    assert any(chunk.content and chunk.segmented_content for chunk in uow.chunks)
    assert uow.version.published_at is not None
    assert uow.version.capabilities == {
        "keyword_search": True,
        "vector_search": False,
        "graph_search": False,
    }
    expected_reasons = {
        None: (),
        "vector": ("EMBEDDING_UNAVAILABLE",),
        "graph": ("GRAPH_UNAVAILABLE",),
    }[enhancement]
    assert uow.version.degraded_reasons == expected_reasons
    assert uow.version.state is (
        KnowledgeVersionState.DEGRADED if enhancement else KnowledgeVersionState.READY
    )
    assert uow.kb.status is KBStatus.READY
    assert uow.kb.vector_degraded is (enhancement == "vector")
    assert uow.kb.active_version_id == uow.version.id
    uow.knowledge_base.update_document_status.assert_awaited_once_with(
        "doc-1", DocStatus.READY, error=None, warning=None, page_count=1
    )
    uow.knowledge_version.fail_candidate.assert_not_awaited()
    if enhancement == "vector":
        embeddings.embed.assert_awaited_once()
        assert embeddings.embed.await_args.kwargs["model_id"] == "model-1"
        assert (
            embeddings.embed.await_args.kwargs["expected_space"].digest
            == uow.version.embedding_space_digest
        )
    else:
        embeddings.embed.assert_not_awaited()
    if graph_llm is not None:
        graph_llm.invoke.assert_not_awaited()
    ocr_llm.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["admission_digest", "provider_model_drift", "strict_policy"])
async def test_embedding_space_failures_never_publish_candidate(failure):
    from app.domain.models.embedding_space import EmbeddingSpaceMismatch

    uow = _CandidateUow()
    space = embedding_space()
    uow.version = uow.version.model_copy(
        update={"embedding_space": space, "embedding_space_digest": space.digest}
    )
    if failure == "strict_policy":
        uow.kb.settings = {"embedding_revision_policy": "verified_only"}
    contents = b"Production releases require approval."
    storage = Mock()
    storage.download_file = AsyncMock(
        return_value=(
            BytesIO(contents),
            File(id="file-1", filename="handbook.txt", mime_type="text/plain", size=len(contents)),
        )
    )
    embeddings = Mock(embed=AsyncMock(side_effect=EmbeddingSpaceMismatch()))
    runner = KBIngestionRunner(
        uow_factory=lambda: uow, file_storage=storage, web_documents=Mock(), embeddings=embeddings
    )
    events = [
        event
        async for event in runner.run_build(
            "build-1",
            policy=initial_execution_policy().knowledge_base.model_copy(
                update={"vector_enabled": True}
            ),
            embedding_model_id=space.model_id,
            embedding_dimensions=space.dimensions,
            embedding_space_digest="0" * 64 if failure == "admission_digest" else space.digest,
        )
    ]
    assert events[-1].kind == "error"
    assert events[-1].failure_code == (
        "EMBEDDING_REVISION_UNVERIFIED"
        if failure == "strict_policy"
        else "EMBEDDING_SPACE_MISMATCH"
    )
    assert uow.kb.active_version_id is None
    uow.knowledge_version.publish_candidate.assert_not_awaited()
    uow.knowledge_base.replace_candidate_chunks.assert_not_awaited()
    uow.knowledge_version.fail_candidate.assert_awaited_once()
    if failure == "provider_model_drift":
        embeddings.embed.assert_awaited_once()
    else:
        embeddings.embed.assert_not_awaited()
