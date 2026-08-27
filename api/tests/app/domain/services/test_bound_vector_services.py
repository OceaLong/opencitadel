from unittest.mock import AsyncMock

import pytest

from app.domain.errors import ConflictError
from app.domain.models.scope import OwnerScope
from app.domain.services.codebase.vector_service import CodebaseVectorService
from app.domain.services.knowledge_base.vector_service import KBVectorService


@pytest.mark.asyncio
@pytest.mark.parametrize("service_type", [KBVectorService, CodebaseVectorService])
async def test_disabled_vector_consumer_does_not_resolve_embedding(service_type) -> None:
    embeddings = AsyncMock()
    service = service_type(
        embeddings,
        scope=OwnerScope.personal("user-1"),
        enabled=False,
    )

    assert await service.embed("query") == []
    assert await service.embed_batch(["a", "b"]) == [[], []]
    embeddings.embed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("service_type", [KBVectorService, CodebaseVectorService])
async def test_enabled_vector_consumer_propagates_missing_binding(service_type) -> None:
    embeddings = AsyncMock()
    embeddings.embed.side_effect = ConflictError("embedding binding missing")
    service = service_type(
        embeddings,
        scope=OwnerScope.personal("user-1"),
        enabled=True,
    )

    with pytest.raises(ConflictError, match="binding missing"):
        await service.embed("query")


@pytest.mark.asyncio
async def test_build_vector_consumer_uses_frozen_model_id() -> None:
    embeddings = AsyncMock()
    embeddings.embed.return_value = [[0.1] * 1536]
    service = KBVectorService(
        embeddings,
        scope=OwnerScope.personal("user-1"),
        enabled=True,
        model_id="embedding-1",
    )

    await service.embed_batch(["content"])

    assert embeddings.embed.await_args.kwargs["model_id"] == "embedding-1"
