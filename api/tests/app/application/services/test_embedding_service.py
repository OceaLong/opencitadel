from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.application.services.embedding_service import EmbeddingService
from app.domain.errors import BadRequestError, ConflictError, ServerRequestsError
from app.domain.models.inference import (
    EmbeddingModelSettings,
    InferenceEndpoint,
    InferenceModel,
    InferenceModelKind,
    InferenceProvider,
    ResolvedInferenceModel,
)
from app.domain.models.scope import OwnerScope


def _embedding_factory(factory):
    return SimpleNamespace(create_embedding=factory)


def _resolved_embedding(
    *,
    model_id: str = "embedding-1",
    max_batch_size: int = 2,
) -> ResolvedInferenceModel:
    return ResolvedInferenceModel(
        endpoint=InferenceEndpoint(
            id="endpoint-1",
            provider=InferenceProvider.OPENAI,
            base_url="https://example.com/v1",
            credential="secret",
        ),
        model=InferenceModel(
            id=model_id,
            endpoint_id="endpoint-1",
            display_name="embedding",
            model_name="text-embedding-3-small",
            kind=InferenceModelKind.EMBEDDING,
            settings=EmbeddingModelSettings(max_batch_size=max_batch_size),
        ),
    )


@pytest.mark.asyncio
async def test_missing_embedding_binding_fails_before_adapter_call() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock(side_effect=ConflictError("binding missing"))
    adapter_factory = Mock()
    service = EmbeddingService(bindings, embedding_factory=_embedding_factory(adapter_factory))

    with pytest.raises(ConflictError, match="binding missing"):
        await service.embed(["hello"], scope=OwnerScope.personal("user-1"))

    adapter_factory.assert_not_called()


@pytest.mark.asyncio
async def test_missing_required_endpoint_credential_fails_before_adapter_call() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock(side_effect=BadRequestError("credential missing"))
    adapter_factory = Mock()
    service = EmbeddingService(bindings, embedding_factory=_embedding_factory(adapter_factory))

    with pytest.raises(BadRequestError, match="credential missing"):
        await service.embed(["hello"], scope=None)

    adapter_factory.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_embedding_dimension_is_rejected() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock(return_value=_resolved_embedding())
    adapter = Mock()
    adapter.embed_batch = AsyncMock(return_value=[[0.1] * 12])
    service = EmbeddingService(
        bindings,
        embedding_factory=_embedding_factory(Mock(return_value=adapter)),
    )

    with pytest.raises(ServerRequestsError, match="1536"):
        await service.embed(["hello"], scope=None)


@pytest.mark.asyncio
async def test_batches_respect_bound_model_max_batch_size() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock(return_value=_resolved_embedding(max_batch_size=2))
    adapter = Mock()
    adapter.embed_batch = AsyncMock(side_effect=[[[0.1] * 1536, [0.2] * 1536], [[0.3] * 1536]])
    service = EmbeddingService(
        bindings,
        embedding_factory=_embedding_factory(Mock(return_value=adapter)),
    )

    vectors = await service.embed(["a", "b", "c"], scope=None)

    assert len(vectors) == 3
    assert [call.args[0] for call in adapter.embed_batch.await_args_list] == [
        ["a", "b"],
        ["c"],
    ]


@pytest.mark.asyncio
async def test_cache_key_includes_model_id() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock(
        side_effect=[
            _resolved_embedding(model_id="embedding-1"),
            _resolved_embedding(model_id="embedding-1"),
            _resolved_embedding(model_id="embedding-2"),
        ]
    )
    first_adapter = Mock()
    first_adapter.embed_batch = AsyncMock(return_value=[[0.1] * 1536])
    second_adapter = Mock()
    second_adapter.embed_batch = AsyncMock(return_value=[[0.2] * 1536])
    adapter_factory = Mock(side_effect=[first_adapter, first_adapter, second_adapter])
    service = EmbeddingService(bindings, embedding_factory=_embedding_factory(adapter_factory))

    first = await service.embed(["same"], scope=None)
    cached = await service.embed(["same"], scope=None)
    changed_model = await service.embed(["same"], scope=None)

    assert first == cached
    assert changed_model != cached
    first_adapter.embed_batch.assert_awaited_once()
    second_adapter.embed_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_response_cardinality_must_match_request() -> None:
    bindings = Mock()
    bindings.resolve = AsyncMock(return_value=_resolved_embedding())
    adapter = Mock()
    adapter.embed_batch = AsyncMock(return_value=[])
    service = EmbeddingService(
        bindings,
        embedding_factory=_embedding_factory(Mock(return_value=adapter)),
    )

    with pytest.raises(ServerRequestsError, match="数量"):
        await service.embed(["hello"], scope=None)
