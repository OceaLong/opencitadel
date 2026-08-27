from unittest.mock import AsyncMock

import pytest

from app.domain.models.codebase import (
    CodebaseChunk,
    CodebaseFile,
    CodebaseSymbol,
    SymbolKind,
)
from app.domain.runtime_policy import (
    CodebaseRetrievalPolicy,
    CodebaseRetrievalRunPolicy,
)
from app.domain.services.codebase.hybrid_retriever import HybridCodeRetriever

_POLICY = CodebaseRetrievalRunPolicy(
    vector_enabled=True,
    retrieval=CodebaseRetrievalPolicy(),
)


def _chunk(
    chunk_id: str = "chunk1",
    *,
    version_id: str = "cbv1",
    file_id: str = "file1",
    symbol_id: str | None = "sym1",
    content: str = "def create_user(): pass",
) -> CodebaseChunk:
    return CodebaseChunk(
        id=chunk_id,
        codebase_id="cb1",
        version_id=version_id,
        file_id=file_id,
        symbol_id=symbol_id,
        content=content,
        search_text=content,
    )


class _Repo:
    def __init__(self) -> None:
        self.search_lexical = AsyncMock(return_value=[])
        self.search_vector = AsyncMock(return_value=[])
        self.list_files = AsyncMock(
            return_value=[
                CodebaseFile(
                    id="file1",
                    codebase_id="cb1",
                    version_id="cbv1",
                    path="src/user_service.py",
                ),
                CodebaseFile(
                    id="file2",
                    codebase_id="cb1",
                    version_id="cbv2",
                    path="src/other.py",
                ),
            ]
        )
        self.list_symbols_by_ids = AsyncMock(
            return_value=[
                CodebaseSymbol(
                    id="sym1",
                    codebase_id="cb1",
                    version_id="cbv1",
                    file_id="file1",
                    name="create_user",
                    kind=SymbolKind.FUNCTION,
                    start_line=10,
                    end_line=12,
                )
            ]
        )


class _Uow:
    def __init__(self, repo: _Repo) -> None:
        self.codebase = repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


class _Vector:
    def __init__(self) -> None:
        self.embed = AsyncMock(return_value=[0.1, 0.2])


@pytest.mark.asyncio
async def test_vector_failure_returns_lexical_results():
    repo = _Repo()
    repo.search_lexical.return_value = [(_chunk(), 0.9)]
    vector = _Vector()
    vector.embed.side_effect = TimeoutError("embedding unavailable")
    retriever = HybridCodeRetriever(
        lambda: _Uow(repo),
        policy=_POLICY,
        vector_service=vector,
    )

    response = await retriever.retrieve("cb1", "cbv1", "create user", limit=5)

    assert response.items[0].path == "src/user_service.py"
    assert response.items[0].lines == (10, 12)
    assert response.items[0].sources == ("lexical",)
    assert response.capabilities["vector_search"] is False
    assert response.capabilities["lexical_search"] is True
    assert response.degraded_reasons == ["EMBEDDING_UNAVAILABLE"]
    repo.search_vector.assert_not_awaited()


@pytest.mark.asyncio
async def test_hybrid_search_is_version_isolated():
    repo = _Repo()
    repo.search_lexical.return_value = [
        (_chunk("legacy", version_id="cbv1"), 0.8),
        (_chunk("foreign", version_id="cbv2", file_id="file2", symbol_id=None), 0.99),
    ]
    repo.search_vector.return_value = [
        (_chunk("vector-foreign", version_id="cbv2", file_id="file2", symbol_id=None), 0.99),
        (_chunk("vector-legacy", version_id="cbv1", symbol_id=None), 0.7),
    ]
    retriever = HybridCodeRetriever(
        lambda: _Uow(repo),
        policy=_POLICY,
        vector_service=_Vector(),
    )

    response = await retriever.retrieve("cb1", "cbv1", "legacyOnly", limit=10)

    assert {item.version_id for item in response.items} == {"cbv1"}
    assert {item.path for item in response.items} == {"src/user_service.py"}


@pytest.mark.asyncio
async def test_rrf_fuses_lexical_and_vector_sources_for_same_chunk():
    repo = _Repo()
    chunk = _chunk()
    repo.search_lexical.return_value = [(chunk, 0.8)]
    repo.search_vector.return_value = [(chunk, 0.7)]
    retriever = HybridCodeRetriever(
        lambda: _Uow(repo),
        policy=_POLICY,
        vector_service=_Vector(),
    )

    response = await retriever.retrieve("cb1", "cbv1", "create user", limit=5)

    assert len(response.items) == 1
    assert response.items[0].sources == ("lexical", "vector")
