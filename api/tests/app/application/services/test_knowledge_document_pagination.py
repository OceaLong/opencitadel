"""Task 7 RED tests for exact immutable document-source pagination."""
from __future__ import annotations

import base64
from dataclasses import FrozenInstanceError, fields
import importlib
import inspect
import json
from types import SimpleNamespace

import pytest

from app.domain.errors import BadRequestError, NotFoundError
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.knowledge_base import (
    ChunkLevel,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.interfaces.endpoints import knowledge_base_routes
from app.interfaces.schemas.knowledge_base import (
    KnowledgeDocumentResponse,
    ReadKnowledgeDocumentResponse,
)


def _document_page_type():
    module = importlib.import_module(
        "app.domain.repositories.knowledge_base_repository"
    )
    return getattr(module, "DocumentPage")


def _document_page_item_type():
    module = importlib.import_module(
        "app.domain.repositories.knowledge_base_repository"
    )
    return getattr(module, "DocumentPageItem")


def _chunk(
    chunk_id: str,
    *,
    page_no: int | None = 1,
    ordinal: int = 1,
    level: ChunkLevel = ChunkLevel.PARENT,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=chunk_id,
        kb_id="kb1",
        version_id="kbv1",
        doc_id="doc1",
        level=level,
        page_no=page_no,
        ordinal=ordinal,
        heading_path=f"heading-{chunk_id}",
        content=f"content-{chunk_id}",
    )


def test_document_page_is_structurally_immutable_and_enforces_cursor_invariant():
    page_type = _document_page_type()
    page = page_type(
        items=[_chunk("a")],
        next_cursor=None,
        total=1,
        truncated=False,
    )

    assert isinstance(page.items, tuple)
    with pytest.raises(FrozenInstanceError):
        page.total = 2
    with pytest.raises(ValueError, match="truncated"):
        page_type(
            items=(),
            next_cursor="next",
            total=0,
            truncated=False,
        )


def test_document_page_items_are_frozen_scalar_snapshots():
    page_type = _document_page_type()
    item_type = _document_page_item_type()
    source = _chunk("a")
    source.embedding.append(0.1)
    page = page_type(
        items=[source],
        next_cursor=None,
        total=1,
        truncated=False,
    )

    item = page.items[0]
    assert type(item) is item_type
    assert {field.name for field in fields(item)} == {
        "id",
        "page_no",
        "heading_path",
        "ordinal",
        "content",
    }
    assert all(
        value is None or isinstance(value, (str, int))
        for value in (
            item.id,
            item.page_no,
            item.heading_path,
            item.ordinal,
            item.content,
        )
    )
    assert not hasattr(item, "embedding")
    with pytest.raises(FrozenInstanceError):
        item.content = "mutated"

    source.id = "changed-id"
    source.content = "changed-content"
    source.embedding.append(0.2)
    assert item.id == "a"
    assert item.content == "content-a"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        pytest.param("id", "", ValueError, id="empty-id"),
        pytest.param("page_no", 0, ValueError, id="zero-page"),
        pytest.param("page_no", -1, ValueError, id="negative-page"),
        pytest.param("ordinal", -1, ValueError, id="negative-ordinal"),
        pytest.param("page_no", True, TypeError, id="boolean-page"),
        pytest.param("ordinal", True, TypeError, id="boolean-ordinal"),
    ],
)
def test_document_page_item_rejects_invalid_identity_and_ordering_values(
    field,
    value,
    error_type,
):
    values = {
        "id": "chunk-1",
        "page_no": 1,
        "heading_path": "heading",
        "ordinal": 0,
        "content": "content",
    }
    values[field] = value

    with pytest.raises(error_type):
        _document_page_item_type()(**values)


class _ChunkRecord:
    def __init__(self, chunk: KnowledgeChunk) -> None:
        self._chunk = chunk

    def to_domain(self) -> KnowledgeChunk:
        return self._chunk


class _CountResult:
    def __init__(self, count: int) -> None:
        self._count = count

    def scalar_one(self) -> int:
        return self._count


class _RowsResult:
    def __init__(self, chunks: list[KnowledgeChunk]) -> None:
        self._chunks = chunks

    def scalars(self):
        return self

    def all(self):
        return [_ChunkRecord(chunk) for chunk in self._chunks]


class _AnchorProbe:
    def __init__(self, value: str | None) -> None:
        self.value = value


class _AnchorResult:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _PageSession:
    def __init__(self, results) -> None:
        self._results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        result = self._results.pop(0)
        if isinstance(result, int):
            return _CountResult(result)
        if isinstance(result, _AnchorProbe):
            return _AnchorResult(result.value)
        return _RowsResult(result)


@pytest.mark.anyio
async def test_exact_first_next_final_pages_have_no_gaps_or_duplicates():
    a = _chunk("a", ordinal=1)
    b = _chunk("b", ordinal=1)
    c = _chunk("c", ordinal=1)
    d = _chunk("d", page_no=2, ordinal=2)
    e = _chunk("e", page_no=3, ordinal=3)
    session = _PageSession([
        5, [a, b, c],
        _AnchorProbe("b"),
        5, [c, d, e],
        _AnchorProbe("d"),
        5, [e],
    ])
    repo = DBKnowledgeBaseRepository(session)

    first = await repo.read_document_page_for_version(
        "kb1", "kbv1", "doc1", "rev1", limit=2,
    )
    second = await repo.read_document_page_for_version(
        "kb1", "kbv1", "doc1", "rev1",
        cursor=first.next_cursor, limit=2,
    )
    final = await repo.read_document_page_for_version(
        "kb1", "kbv1", "doc1", "rev1",
        cursor=second.next_cursor, limit=2,
    )

    assert [item.id for page in (first, second, final)
            for item in page.items] == ["a", "b", "c", "d", "e"]
    assert [page.total for page in (first, second, final)] == [5, 5, 5]
    assert [page.truncated for page in (first, second, final)] == [
        True, True, False,
    ]
    assert first.next_cursor
    assert second.next_cursor
    assert final.next_cursor is None


@pytest.mark.anyio
async def test_document_page_filters_parents_before_limit_and_uses_exact_closure():
    session = _PageSession([2, [_chunk("a"), _chunk("b")]])
    repo = DBKnowledgeBaseRepository(session)

    page = await repo.read_document_page_for_version(
        "kb1", "kbv1", "doc1", "rev1", page_no=1, limit=2,
    )

    assert all(type(item) is _document_page_item_type()
               for item in page.items)
    count_stmt, page_stmt = session.statements
    count_sql = str(count_stmt).lower()
    page_sql = str(page_stmt).lower()
    params = {
        **count_stmt.compile().params,
        **page_stmt.compile().params,
    }
    for sql in (count_sql, page_sql):
        assert "knowledge_base_versions" in sql
        assert "knowledge_base_version_documents" in sql
        assert "knowledge_document_revisions" in sql
        assert "published_at is not null" in sql
        assert "knowledge_chunks.level" in sql
        assert "knowledge_document_revisions.id" in sql
        assert "knowledge_document_revisions.state" in sql
        assert "knowledge_base_version_documents.state" in sql
        assert "active_version_id" not in sql
        assert "knowledge_chunks.version_id is null" not in sql
    assert ChunkLevel.PARENT.value in params.values()
    assert "rev1" in params.values()
    assert "kbv1" in params.values()
    assert "doc1" in params.values()
    assert " offset " not in f" {page_sql} "
    assert "knowledge_chunks.page_no asc" in page_sql
    assert "knowledge_chunks.ordinal asc" in page_sql
    assert "knowledge_chunks.id asc" in page_sql
    assert any(value == 3 for value in page_stmt.compile().params.values())


@pytest.mark.anyio
async def test_empty_page_keeps_exact_pre_cursor_total():
    repo = DBKnowledgeBaseRepository(_PageSession([7, []]))

    page = await repo.read_document_page_for_version(
        "kb1", "kbv1", "doc1", "rev1", page_no=99, limit=20,
    )

    assert page.items == ()
    assert page.total == 7
    assert page.next_cursor is None
    assert page.truncated is False


def _cursor_payload(cursor: str) -> dict:
    padding = "=" * (-len(cursor) % 4)
    return json.loads(
        base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
    )


def _encode_cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")


class _NoExecuteSession:
    async def execute(self, _statement):
        raise AssertionError("invalid cursor must fail before database access")


class _RejectAnchorResult:
    def scalar_one_or_none(self):
        return None

    def scalar_one(self):
        return 2

    def scalars(self):
        return self

    def all(self):
        return []


class _RejectAnchorSession:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _RejectAnchorResult()


def _bound_cursor(key: tuple[int | None, int, str]) -> str:
    return _encode_cursor({
        "kb": "kb1",
        "version": "kbv1",
        "document": "doc1",
        "revision": "rev1",
        "page": None,
        "key": list(key),
    })


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key",
    [
        pytest.param((999, 999, "invented-anchor"), id="invented"),
        pytest.param((2, 1, "real-parent"), id="altered-page"),
        pytest.param((1, 99, "real-parent"), id="altered-ordinal"),
        pytest.param((1, 2, "child-anchor"), id="child"),
        pytest.param((1, 1, "other-version-anchor"), id="other-version"),
        pytest.param((1, 1, "other-revision-anchor"), id="other-revision"),
    ],
)
async def test_cursor_anchor_must_exist_in_exact_parent_revision_before_count(
    key,
):
    session = _RejectAnchorSession()
    repo = DBKnowledgeBaseRepository(session)

    with pytest.raises(ValueError, match="cursor"):
        await repo.read_document_page_for_version(
            "kb1",
            "kbv1",
            "doc1",
            "rev1",
            cursor=_bound_cursor(key),
            limit=1,
        )

    assert len(session.statements) == 1
    anchor_stmt = session.statements[0]
    anchor_sql = str(anchor_stmt).lower()
    anchor_values = list(anchor_stmt.compile().params.values())
    assert "count(" not in anchor_sql
    assert "knowledge_base_versions" in anchor_sql
    assert "knowledge_base_version_documents" in anchor_sql
    assert "knowledge_document_revisions" in anchor_sql
    assert "published_at is not null" in anchor_sql
    assert "knowledge_chunks.level" in anchor_sql
    assert "knowledge_chunks.id" in anchor_sql
    assert "knowledge_chunks.ordinal" in anchor_sql
    assert ChunkLevel.PARENT.value in anchor_values
    assert "kb1" in anchor_values
    assert "kbv1" in anchor_values
    assert "doc1" in anchor_values
    assert "rev1" in anchor_values
    assert key[1] in anchor_values
    assert key[2] in anchor_values


@pytest.mark.anyio
async def test_cursor_rejects_malformed_every_wrong_identity_and_invalid_key_type():
    seed_repo = DBKnowledgeBaseRepository(
        _PageSession([2, [_chunk("a"), _chunk("b")]])
    )
    valid = (
        await seed_repo.read_document_page_for_version(
            "kb1", "kbv1", "doc1", "rev1", page_no=1, limit=1,
        )
    ).next_cursor
    assert valid is not None

    bad_requests = [
        ("wrong-kb", "kbv1", "doc1", "rev1", 1, valid),
        ("kb1", "wrong-version", "doc1", "rev1", 1, valid),
        ("kb1", "kbv1", "wrong-doc", "rev1", 1, valid),
        ("kb1", "kbv1", "doc1", "wrong-revision", 1, valid),
        ("kb1", "kbv1", "doc1", "rev1", 2, valid),
        ("kb1", "kbv1", "doc1", "rev1", 1, "not-base64!"),
    ]
    payload = _cursor_payload(valid)
    payload["key"][1] = "not-an-integer"
    bad_requests.append((
        "kb1", "kbv1", "doc1", "rev1", 1, _encode_cursor(payload),
    ))
    wrong_key_page = _cursor_payload(valid)
    wrong_key_page["key"][0] = 99
    bad_requests.append((
        "kb1",
        "kbv1",
        "doc1",
        "rev1",
        1,
        _encode_cursor(wrong_key_page),
    ))

    for kb_id, version_id, doc_id, revision_id, page_no, cursor in (
        bad_requests
    ):
        repo = DBKnowledgeBaseRepository(_NoExecuteSession())
        with pytest.raises(ValueError, match="cursor"):
            await repo.read_document_page_for_version(
                kb_id,
                version_id,
                doc_id,
                revision_id,
                page_no=page_no,
                cursor=cursor,
                limit=1,
            )


class _ServiceRepo:
    def __init__(self, *, authorized: bool = True) -> None:
        self.authorized = authorized
        self.calls = []
        self.kb = KnowledgeBase(
            id="kb1",
            name="KB",
            active_version_id="kbv2",
        )
        self.doc = KnowledgeDocument(
            id="doc1",
            kb_id="kb1",
            title="Historical",
        )

    async def get_kb(self, kb_id, scope=None):
        self.calls.append(("get_kb", kb_id, scope))
        return self.kb if self.authorized and kb_id == "kb1" else None

    async def count_ready_documents(self, kb_ids):
        self.calls.append(("count_ready_documents", tuple(kb_ids)))
        return {"kb1": 1}

    async def get_document_for_version(self, kb_id, version_id, doc_id):
        self.calls.append((
            "get_document_for_version", kb_id, version_id, doc_id,
        ))
        if (kb_id, version_id, doc_id) == ("kb1", "kbv1", "doc1"):
            return self.doc, "rev1"
        return None

    async def read_document_page_for_version(
        self,
        kb_id,
        version_id,
        doc_id,
        revision_id,
        *,
        page_no=None,
        cursor=None,
        limit=30,
    ):
        self.calls.append((
            "read_page", kb_id, version_id, doc_id, revision_id,
            page_no, cursor, limit,
        ))
        return _document_page_type()(
            items=(_chunk("a"),),
            next_cursor="next",
            total=2,
            truncated=True,
        )


class _Uow:
    def __init__(self, repo) -> None:
        self.knowledge_base = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.anyio
async def test_service_authorizes_owner_then_reads_exact_historical_revision():
    repo = _ServiceRepo()
    service = KnowledgeBaseService(
        uow_factory=lambda: _Uow(repo),
        file_storage=SimpleNamespace(),
    )
    scope = OwnerScope.personal("u1")

    doc, revision_id, page = await service.read_document_page(
        "kb1",
        "kbv1",
        "doc1",
        page=1,
        cursor="opaque",
        limit=1,
        scope=scope,
    )

    assert repo.kb.active_version_id == "kbv2"
    assert doc.title == "Historical"
    assert revision_id == "rev1"
    assert page.total == 2
    assert repo.calls[0] == ("get_kb", "kb1", scope)
    assert (
        "get_document_for_version", "kb1", "kbv1", "doc1",
    ) in repo.calls
    assert (
        "read_page", "kb1", "kbv1", "doc1", "rev1",
        1, "opaque", 1,
    ) in repo.calls


@pytest.mark.anyio
async def test_service_rejects_unauthorized_owner_before_exact_document_read():
    repo = _ServiceRepo(authorized=False)
    service = KnowledgeBaseService(
        uow_factory=lambda: _Uow(repo),
        file_storage=SimpleNamespace(),
    )

    with pytest.raises(NotFoundError):
        await service.read_document_page(
            "kb1", "kbv1", "doc1", scope=OwnerScope.personal("attacker"),
        )

    assert not any(call[0] == "get_document_for_version" for call in repo.calls)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("page", "limit"),
    [(0, 30), (-1, 30), (None, 0), (None, 201)],
)
async def test_service_rejects_unbounded_page_inputs(page, limit):
    repo = _ServiceRepo()
    service = KnowledgeBaseService(
        uow_factory=lambda: _Uow(repo),
        file_storage=SimpleNamespace(),
    )

    with pytest.raises(BadRequestError):
        await service.read_document_page(
            "kb1", "kbv1", "doc1", page=page, limit=limit,
        )


def test_legacy_document_response_remains_additively_compatible():
    document = KnowledgeDocumentResponse.model_validate(
        KnowledgeDocument(id="doc1", kb_id="kb1", title="Legacy"),
        from_attributes=True,
    )

    response = ReadKnowledgeDocumentResponse(
        document=document,
        content="legacy parent content",
    )

    assert response.content == "legacy parent content"
    assert response.items == []
    assert response.next_cursor is None
    assert response.total == 0
    assert response.truncated is False


@pytest.mark.anyio
async def test_version_content_route_projects_page_and_owner_scope():
    page = _document_page_type()(
        items=(_chunk("a"),),
        next_cursor="next",
        total=2,
        truncated=True,
    )
    scope = OwnerScope.personal("u1")
    ctx = WorkspaceContext(
        principal=Principal(user_id="u1"),
        scope=scope,
    )

    class Service:
        async def read_document_page(self, *args, **kwargs):
            assert args == ("kb1", "kbv1", "doc1")
            assert kwargs == {
                "page": 1,
                "cursor": "cursor",
                "limit": 1,
                "scope": scope,
            }
            return (
                KnowledgeDocument(
                    id="doc1", kb_id="kb1", title="Historical",
                ),
                "rev1",
                page,
            )

    response = await knowledge_base_routes.read_document_version_content(
        "kb1",
        "kbv1",
        "doc1",
        1,
        "cursor",
        1,
        ctx,
        Service(),
    )

    assert response.data.version_id == "kbv1"
    assert response.data.document_revision_id == "rev1"
    assert [item.id for item in response.data.items] == ["a"]
    assert response.data.content == "content-a"
    assert response.data.next_cursor == "next"
    assert response.data.total == 2
    assert response.data.truncated is True


def test_version_content_route_declares_bounded_query_contract():
    signature = inspect.signature(
        knowledge_base_routes.read_document_version_content
    )
    assert signature.parameters["page"].default.default is None
    assert "Ge(ge=1)" in repr(
        signature.parameters["page"].default.metadata
    )
    assert signature.parameters["limit"].default.default == 30
    limit_metadata = repr(signature.parameters["limit"].default.metadata)
    assert "Ge(ge=1)" in limit_metadata
    assert "Le(le=200)" in limit_metadata
    cursor = signature.parameters["cursor"].default
    assert cursor.default is None
    assert "MinLen(min_length=1)" in repr(cursor.metadata)
    assert "MaxLen(max_length=2048)" in repr(cursor.metadata)
