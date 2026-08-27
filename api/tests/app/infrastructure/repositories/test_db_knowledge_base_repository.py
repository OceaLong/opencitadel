from types import SimpleNamespace

import pytest

from app.domain.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeChunk,
)
from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_knowledge_base_repository import DBKnowledgeBaseRepository
from app.infrastructure.repositories.kb._shared import (
    build_versioned_vector_search_statement,
)


class _RecordingSession:
    """记录 execute 调用的假 AsyncSession。"""

    def __init__(self, results=None):
        self.calls: list[tuple[str, object]] = []  # (sql_text, params)
        self.added: list[object] = []
        self._results = list(results or [])

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return None

    def add(self, obj):
        self.added.append(obj)


def _chunk(i: int, with_embedding: bool) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=f"c{i}",
        kb_id="kb1",
        doc_id="d1",
        version_id="v1",
        content=f"content {i}",
        embedding=[0.1, 0.2] if with_embedding else [],
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_save_chunks_batches_by_500_and_embedding_presence():
    session = _RecordingSession()
    repo = DBKnowledgeBaseRepository(db_session=session)
    chunks = [_chunk(i, with_embedding=True) for i in range(1200)]
    chunks += [_chunk(2000 + i, with_embedding=False) for i in range(700)]

    await repo.save_chunks(chunks)

    # 1200 条带向量 -> 3 批；700 条无向量 -> 2 批
    assert len(session.calls) == 5
    embed_calls = [c for c in session.calls if "CAST(:embedding AS vector)" in c[0]]
    plain_calls = [c for c in session.calls if "CAST(:embedding AS vector)" not in c[0]]
    assert [len(params) for _, params in embed_calls] == [500, 500, 200]
    assert [len(params) for _, params in plain_calls] == [500, 200]
    # executemany 参数为字典列表，且键完整
    first_params = embed_calls[0][1]
    assert isinstance(first_params, list)
    assert set(first_params[0].keys()) == {
        "id",
        "kb_id",
        "doc_id",
        "parent_id",
        "level",
        "content",
        "version_id",
        "segmented_content",
        "content_tsv",
        "page_no",
        "heading_path",
        "ordinal",
        "embedding",
    }


@pytest.mark.anyio
async def test_save_chunks_empty_is_noop():
    session = _RecordingSession()
    repo = DBKnowledgeBaseRepository(db_session=session)
    await repo.save_chunks([])
    assert session.calls == []


@pytest.mark.anyio
async def test_delete_kb_removes_index_and_restricted_revision_chain_first():
    session = _RecordingSession()
    repo = DBKnowledgeBaseRepository(db_session=session)

    await repo.delete_kb("kb1")

    statements = [sql for sql, _params in session.calls]
    assert len(statements) == 7
    assert "DELETE FROM knowledge_relations" in statements[0]
    assert "DELETE FROM knowledge_entity_refs" in statements[1]
    assert "DELETE FROM knowledge_entities" in statements[2]
    assert "DELETE FROM knowledge_chunks" in statements[3]
    assert "DELETE FROM knowledge_base_version_documents" in statements[4]
    assert "knowledge_base_version_documents.knowledge_base_id" in statements[4]
    assert "DELETE FROM knowledge_document_revisions" in statements[5]
    assert "SELECT knowledge_documents.id" in statements[5]
    assert "knowledge_documents.kb_id" in statements[5]
    assert "DELETE FROM knowledge_bases" in statements[6]


@pytest.mark.anyio
async def test_delete_document_removes_index_manifest_and_revision_chain_first():
    session = _RecordingSession(
        results=[
            _FakeResult(),
            _FakeResult(scalars_all=[]),
            _FakeResult(),
            _FakeResult(),
            _FakeResult(),
            _FakeResult(),
            _FakeResult(),
        ]
    )
    repo = DBKnowledgeBaseRepository(db_session=session)

    await repo.delete_document("d1")

    statements = [sql for sql, _params in session.calls]
    assert len(statements) == 7
    assert "DELETE FROM knowledge_relations" in statements[0]
    assert "SELECT DISTINCT knowledge_entity_refs.entity_id" in statements[1]
    assert "DELETE FROM knowledge_entity_refs" in statements[2]
    assert "DELETE FROM knowledge_chunks" in statements[3]
    assert "DELETE FROM knowledge_base_version_documents" in statements[4]
    assert "DELETE FROM knowledge_document_revisions" in statements[5]
    assert "DELETE FROM knowledge_documents" in statements[6]


@pytest.mark.anyio
@pytest.mark.parametrize("with_embedding", [False, True])
async def test_save_chunks_types_nullable_tsvector_bind(with_embedding):
    session = _RecordingSession()
    repo = DBKnowledgeBaseRepository(db_session=session)

    await repo.save_chunks([_chunk(1, with_embedding=with_embedding)])

    sql = " ".join(session.calls[0][0].split())
    assert "COALESCE(" in sql
    assert "CAST(:content_tsv AS tsvector)" in sql
    assert "to_tsvector('simple', :segmented_content)" in sql
    assert "WHEN :content_tsv IS NULL" not in sql


class _FakeResult:
    def __init__(self, scalars_all=None, rows=None, scalar_one=0):
        self._scalars_all = scalars_all or []
        self._rows = rows or []
        self._scalar_one = scalar_one

    def scalars(self):
        outer = self

        class _S:
            def all(self):
                return outer._scalars_all

        return _S()

    def all(self):
        return self._rows

    def fetchall(self):
        return self._rows

    def scalar_one(self):
        return self._scalar_one

    def scalar_one_or_none(self):
        return self._scalar_one


@pytest.mark.anyio
async def test_get_kb_for_update_uses_owner_scoped_row_lock():
    expected = KnowledgeBase(
        id="kb1",
        name="locked",
        owner_user_id="user1",
    )
    record = SimpleNamespace(to_domain=lambda: expected)
    session = _RecordingSession(results=[_FakeResult(scalar_one=record)])
    repo = DBKnowledgeBaseRepository(db_session=session)

    result = await repo.get_kb_for_update(
        "kb1",
        scope=OwnerScope.personal("user1"),
    )

    assert result == expected
    sql, _ = session.calls[0]
    assert "FOR UPDATE" in sql
    assert "owner_user_id" in sql


@pytest.mark.anyio
async def test_purge_documents_deletes_zero_ref_entities_from_candidates_only():
    # execute 依次: 删关系 -> 查候选 entity_id -> 删引用 -> 删归零实体 -> 删 chunks
    session = _RecordingSession(
        results=[
            _FakeResult(),  # delete relations
            _FakeResult(scalars_all=["e1", "e2"]),  # select candidate entity ids
            _FakeResult(),  # delete refs
            _FakeResult(),  # delete zero-ref entities
            _FakeResult(),  # delete chunks
        ]
    )
    repo = DBKnowledgeBaseRepository(db_session=session)

    await repo.purge_documents_index_data(["d1"])

    assert len(session.calls) == 5
    sql_texts = [sql for sql, _ in session.calls]
    assert "knowledge_relations" in sql_texts[0]
    assert "knowledge_entity_refs" in sql_texts[2]
    assert "knowledge_entities" in sql_texts[3]
    assert "knowledge_chunks" in sql_texts[4]


@pytest.mark.anyio
async def test_purge_documents_without_candidates_skips_entity_delete():
    session = _RecordingSession(
        results=[
            _FakeResult(),  # delete relations
            _FakeResult(scalars_all=[]),  # no candidates
            _FakeResult(),  # delete refs
            _FakeResult(),  # delete chunks
        ]
    )
    repo = DBKnowledgeBaseRepository(db_session=session)
    await repo.purge_documents_index_data(["d1"])
    assert len(session.calls) == 4
    assert not any("DELETE FROM knowledge_entities" in sql for sql, _ in session.calls)


@pytest.mark.anyio
async def test_count_ready_documents_groups_by_kb():
    session = _RecordingSession(results=[_FakeResult(rows=[("kb1", 3), ("kb2", 0)])])
    repo = DBKnowledgeBaseRepository(db_session=session)
    counts = await repo.count_ready_documents(["kb1", "kb2"])
    assert counts == {"kb1": 3, "kb2": 0}


@pytest.mark.anyio
async def test_list_documents_page_returns_items_and_total():
    doc_record = SimpleNamespace(to_domain=lambda: "DOC")
    session = _RecordingSession(
        results=[_FakeResult(scalars_all=[doc_record]), _FakeResult(scalar_one=7)]
    )
    repo = DBKnowledgeBaseRepository(db_session=session)
    items, total = await repo.list_documents_page("kb1", limit=5, offset=0)
    assert items == ["DOC"]
    assert total == 7


@pytest.mark.anyio
async def test_versioned_bm25_sql_closes_version_manifest_and_revision():
    session = _RecordingSession(results=[_FakeResult(rows=[])])
    repo = DBKnowledgeBaseRepository(db_session=session)

    assert await repo.bm25_search_chunks_for_version("kb1", "kbv1", "release", limit=7) == []

    sql, params = session.calls[0]
    assert "c.version_id = :version_id" in sql
    assert "version.state IN ('ready', 'degraded')" in sql
    assert "version.published_at IS NOT NULL" in sql
    assert "knowledge_base_version_documents manifest" in sql
    assert "manifest.document_revision_id" in sql
    assert "knowledge_document_revisions revision" in sql
    assert "c.version_id IS NULL" not in sql
    assert "active_version_id" not in sql
    assert params == {
        "kb_id": "kb1",
        "version_id": "kbv1",
        "query": "release",
        "limit": 7,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "embedding",
    [[], [float("nan")], ["bad"], [0.1] * 1535, [0.1] * 1537],
)
async def test_versioned_vector_sql_rejects_empty_or_malformed_embedding(
    embedding,
):
    session = _RecordingSession()
    repo = DBKnowledgeBaseRepository(db_session=session)

    with pytest.raises(ValueError, match="embedding"):
        await repo.vector_search_chunks_for_version("kb1", "kbv1", embedding, limit=7)

    assert session.calls == []


@pytest.mark.anyio
async def test_versioned_vector_sql_enables_iterative_scan_before_query():
    session = _RecordingSession(results=[_FakeResult(), _FakeResult(rows=[])])
    repo = DBKnowledgeBaseRepository(db_session=session)

    assert (
        await repo.vector_search_chunks_for_version(
            "kb1",
            "kbv1",
            [0.0] * 1536,
            limit=10,
        )
        == []
    )

    assert session.calls[0][0].strip() == ("SET LOCAL hnsw.iterative_scan = 'strict_order'")
    query_sql, query_params = session.calls[1]
    assert "c.version_id = :version_id" in query_sql
    assert "ORDER BY c.embedding <=> CAST(:query AS vector)" in query_sql
    assert query_params["version_id"] == "kbv1"


def test_versioned_vector_sql_and_explain_share_production_statement():
    production = str(build_versioned_vector_search_statement())
    explained = str(build_versioned_vector_search_statement(explain=True))
    assert production in explained
    assert explained.lstrip().startswith("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)")
    for predicate in (
        "c.kb_id = :kb_id",
        "c.version_id = :version_id",
        "version.state IN ('ready', 'degraded')",
        "version.published_at IS NOT NULL",
        "manifest.state = 'indexed'",
        "revision.state = 'indexed'",
        "ix_kb_chunks_embedding",
    ):
        if predicate == "ix_kb_chunks_embedding":
            continue
        assert predicate in production
