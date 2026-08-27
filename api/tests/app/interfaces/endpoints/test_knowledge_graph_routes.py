"""Task 6 published graph API contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.errors import BadRequestError
from app.domain.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeEntity,
    KnowledgeGraphNode,
    KnowledgeGraphResponse,
    KnowledgeRelation,
)
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.scope import (
    OwnerScope,
    Principal,
    WorkspaceContext,
)
from app.interfaces.auth_dependencies import get_workspace_context
from app.interfaces.endpoints.knowledge_base_routes import router
from app.interfaces.service_dependencies import get_knowledge_base_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Storage:
    pass


class _KbRepo:
    def __init__(self, *, capability: bool = True):
        self.capability = capability
        self.scope = None
        self.page_calls = []

    async def get_kb(self, kb_id, scope=None):
        self.scope = scope
        if kb_id != "kb1":
            return None
        return KnowledgeBase(id="kb1", name="KB", owner_user_id="u1", active_version_id="v2")

    async def count_ready_documents(self, kb_ids):
        return {"kb1": 1}

    async def list_entities_page_for_version(self, kb_id, version_id, *, q, after, limit):
        self.page_calls.append((kb_id, version_id, q, after, limit))
        return (
            [
                KnowledgeEntity(
                    id="e1",
                    kb_id="kb1",
                    version_id="v1",
                    name="OpenCitadel",
                    normalized_name="opencitadel",
                    type="product",
                )
            ],
            ("opencitadel", "e1"),
        )

    async def list_relations_for_entities_for_version(self, kb_id, version_id, entity_ids):
        return [
            KnowledgeRelation(
                id="r1",
                kb_id="kb1",
                version_id="v1",
                src_entity_id="e1",
                dst_entity_id="e2",
                relation="uses",
                chunk_id="chunk1",
            )
        ]

    async def get_entities_by_ids_for_version(self, kb_id, version_id, entity_ids):
        return [
            KnowledgeEntity(
                id="e2",
                kb_id="kb1",
                version_id="v1",
                name="RAG",
                normalized_name="rag",
                type="concept",
            )
        ]

    async def get_chunks_by_ids_for_version(self, kb_id, version_id, chunk_ids):
        from app.domain.models.knowledge_base import (
            KnowledgeChunk,
            KnowledgeDocument,
        )
        from app.domain.repositories.knowledge_base_repository import (
            VersionedKnowledgeChunk,
        )

        return [
            VersionedKnowledgeChunk(
                chunk=KnowledgeChunk(
                    id="chunk1",
                    kb_id="kb1",
                    version_id="v1",
                    doc_id="doc1",
                    page_no=4,
                ),
                document=KnowledgeDocument(id="doc1", kb_id="kb1", title="Handbook"),
                document_revision_id="rev1",
            )
        ]


class _VersionRepo:
    def __init__(self, capability=True):
        self.capability = capability

    async def get_version(self, version_id, *, knowledge_base_id=None):
        return KnowledgeBaseVersion(
            id=version_id,
            knowledge_base_id=knowledge_base_id,
            state=(
                KnowledgeVersionState.READY if self.capability else KnowledgeVersionState.DEGRADED
            ),
            capabilities={"graph_search": self.capability},
            degraded_reasons=(() if self.capability else ("GRAPH_PARTIAL",)),
            published_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )


class _Uow:
    def __init__(self, kb_repo, version_repo):
        self.knowledge_base = kb_repo
        self.knowledge_version = version_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _service(kb_repo, version_repo):
    return KnowledgeBaseService(
        lambda: _Uow(kb_repo, version_repo),
        _Storage(),
        run_admission_service=SimpleNamespace(),
        run_control_service=SimpleNamespace(),
        run_projection=SimpleNamespace(),
        web_documents=SimpleNamespace(),
    )


@pytest.mark.anyio
async def test_owner_scoped_graph_has_every_endpoint_and_exact_evidence():
    repo = _KbRepo()
    scope = OwnerScope.personal("u1")
    response = await _service(repo, _VersionRepo()).get_version_graph(
        "kb1",
        "v1",
        q="Open",
        cursor=None,
        limit=10,
        scope=scope,
    )
    assert repo.scope == scope
    assert response.capability is True
    assert {node.id for node in response.nodes} == {"e1", "e2"}
    assert all(
        edge.source in {node.id for node in response.nodes}
        and edge.target in {node.id for node in response.nodes}
        for edge in response.edges
    )
    assert response.edges[0].evidence[0].model_dump() == {
        "version_id": "v1",
        "document_revision_id": "rev1",
        "doc_id": "doc1",
        "page_no": 4,
        "chunk_id": "chunk1",
    }
    assert response.next_cursor


@pytest.mark.anyio
async def test_capability_false_suppresses_partial_rows():
    repo = _KbRepo(capability=False)
    response = await _service(repo, _VersionRepo(capability=False)).get_version_graph(
        "kb1",
        "v1",
        q=None,
        cursor=None,
        limit=10,
        scope=OwnerScope.personal("u1"),
    )
    assert response.capability is False
    assert response.nodes == ()
    assert response.edges == ()
    assert repo.page_calls == []


@pytest.mark.anyio
async def test_capability_false_still_rejects_malformed_cursor():
    with pytest.raises(BadRequestError, match="cursor"):
        await _service(
            _KbRepo(capability=False),
            _VersionRepo(capability=False),
        ).get_version_graph(
            "kb1",
            "v1",
            cursor="malformed",
            limit=10,
            scope=OwnerScope.personal("u1"),
        )


@pytest.mark.anyio
async def test_malformed_and_wrong_version_cursor_are_rejected():
    service = _service(_KbRepo(), _VersionRepo())
    with pytest.raises(BadRequestError, match="cursor"):
        await service.get_version_graph(
            "kb1",
            "v1",
            cursor="malformed",
            limit=10,
            scope=OwnerScope.personal("u1"),
        )
    first = await service.get_version_graph(
        "kb1",
        "v1",
        limit=10,
        scope=OwnerScope.personal("u1"),
    )
    with pytest.raises(BadRequestError, match="cursor"):
        await service.get_version_graph(
            "kb1",
            "v2",
            cursor=first.next_cursor,
            limit=10,
            scope=OwnerScope.personal("u1"),
        )


def test_real_fastapi_route_uses_workspace_context_scope():
    expected_scope = OwnerScope.personal("u1")

    class Service:
        async def get_version_graph(self, kb_id, version_id, **kwargs):
            assert (kb_id, version_id) == ("kb1", "v1")
            assert kwargs == {
                "q": "Open",
                "cursor": None,
                "limit": 7,
                "scope": expected_scope,
            }
            return KnowledgeGraphResponse(
                capability=True,
                nodes=(
                    KnowledgeGraphNode(
                        id="e1",
                        name="OpenCitadel",
                        type="product",
                    ),
                ),
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=Principal(user_id="u1"),
        scope=expected_scope,
    )
    app.dependency_overrides[get_knowledge_base_service] = Service

    response = TestClient(app).get(
        "/knowledge-bases/kb1/versions/v1/graph",
        params={"q": "Open", "limit": 7},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["capability"] is True
    assert body["nodes"][0]["name"] == "OpenCitadel"
