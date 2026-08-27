from unittest.mock import AsyncMock

import pytest

from app.domain.models.codebase import CodebaseFile, CodebaseSymbol, SymbolKind
from app.domain.runtime_policy import CodebaseAnalysisPolicy
from app.domain.services.codebase.indexer import CodebaseIndexer
from app.domain.services.codebase.lexical_indexer import CodebaseLexicalIndexer


def test_lexical_document_splits_identifiers():
    indexer = CodebaseLexicalIndexer()

    text = indexer.search_text(
        path="src/user_service.py",
        symbols=["createUser"],
        content="def create_user(): pass",
    )

    assert {
        "user",
        "service",
        "create",
        "createuser",
        "create_user",
    } <= set(text.split())


@pytest.mark.asyncio
async def test_codebase_indexer_keeps_mandatory_lexical_chunks_when_embedding_fails():
    vector = AsyncMock()
    vector.enabled = True
    vector.embed_batch.side_effect = TimeoutError("embedding unavailable")
    file = CodebaseFile(
        id="file1",
        codebase_id="cb1",
        version_id="cbv1",
        path="src/user_service.py",
        language="python",
    )
    symbol = CodebaseSymbol(
        id="sym1",
        codebase_id="cb1",
        version_id="cbv1",
        file_id=file.id,
        name="createUser",
        qualified_name="UserService.createUser",
        kind=SymbolKind.FUNCTION,
        signature="def create_user(name: str)",
        start_line=1,
        end_line=2,
    )

    chunks = await CodebaseIndexer(
        policy=CodebaseAnalysisPolicy(),
        vector_service=vector,
    ).build_chunks(
        "cb1",
        [file],
        [symbol],
        {"src/user_service.py": "def create_user(name):\n    return name\n"},
        version_id="cbv1",
    )

    assert chunks
    assert all(chunk.version_id == "cbv1" for chunk in chunks)
    assert all(chunk.search_text.strip() for chunk in chunks)
    assert all(not chunk.embedding for chunk in chunks)
    assert "create_user" in chunks[0].search_text
