"""Embedding identity integration through real services and scoped consumers.

Provider transport is controlled; PostgreSQL space filtering is exercised in
test_db_memory_vector_search (including a same-dimension foreign-space row).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.application.services.embedding_service import EmbeddingService
from app.domain.errors import ForbiddenError, ServerRequestsError
from app.domain.models.embedding_space import (
    EmbeddingRevisionUnverified,
    EmbeddingSpaceDescriptor,
    EmbeddingSpaceMismatch,
)
from app.domain.models.scope import OwnerScope
from app.domain.services.knowledge_base.vector_service import KBVectorService
from tests.app.embedding_test_support import resolved_embedding


def _service(resolved):
    bindings = SimpleNamespace(resolve=AsyncMock(return_value=resolved))
    models = SimpleNamespace(resolve_model=AsyncMock(return_value=resolved))
    adapter = SimpleNamespace(
        embed_batch=AsyncMock(return_value=[[0.1] * 1536]), aclose=AsyncMock()
    )
    factory = SimpleNamespace(create_embedding=Mock(return_value=adapter))
    return EmbeddingService(bindings, factory, models), bindings, models, adapter, factory


@pytest.mark.asyncio
async def test_kb_same_dimensions_different_space():
    original = resolved_embedding()
    space = EmbeddingSpaceDescriptor.from_resolved(original)
    service, bindings, models, adapter, _ = _service(original)
    bindings.resolve.return_value = resolved_embedding("model-b", "embedding-b")
    vector = KBVectorService(service, scope=OwnerScope.personal("u"), enabled=True)
    assert len(await vector.embed("question", expected_space=space)) == 1536
    bindings.resolve.assert_not_awaited()
    models.resolve_model.assert_awaited_once_with(space.model_id, scope=OwnerScope.personal("u"))
    adapter.embed_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_mutated_model_id_cannot_reuse_vectors_or_cache():
    original = resolved_embedding()
    space = EmbeddingSpaceDescriptor.from_resolved(original)
    service, _, models, adapter, _ = _service(original)
    scope = OwnerScope.personal("u")
    await service.embed(["private fact"], scope=scope, expected_space=space)
    changed = original.model_copy(deep=True)
    changed.model.model_name = "same-dimension-different-model"
    models.resolve_model.return_value = changed
    with pytest.raises(EmbeddingSpaceMismatch):
        await service.embed(["private fact"], scope=scope, expected_space=space)
    adapter.embed_batch.assert_awaited_once()
    assert EmbeddingSpaceDescriptor.from_resolved(changed).digest != space.digest


@pytest.mark.asyncio
async def test_credentials_or_price_do_not_change_space(monkeypatch):
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding

    original = resolved_embedding()
    space = EmbeddingSpaceDescriptor.from_resolved(original)
    create = AsyncMock(
        return_value=SimpleNamespace(
            data=[SimpleNamespace(index=0, embedding=[0.1] * 1536)],
            model_dump=lambda: {"usage": {"prompt_tokens": 2, "total_tokens": 2}},
        )
    )
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            embeddings=SimpleNamespace(create=create), close=AsyncMock()
        ),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    guard = SimpleNamespace(before_send=AsyncMock(return_value="permit"), after_send=AsyncMock())
    models = SimpleNamespace(resolve_model=AsyncMock(return_value=original))
    service = EmbeddingService(
        Mock(),
        SimpleNamespace(create_embedding=OpenAICompatibleEmbedding),
        models,
        physical_dispatch=SimpleNamespace(guard=Mock(return_value=guard)),
    )
    scope = OwnerScope.personal("u")
    first = await service.embed(["fact"], scope=scope, expected_space=space)
    changed = original.model_copy(deep=True)
    changed.endpoint.credential = "rotated-secret"
    changed.model.input_price_per_million = 42
    changed.model.settings.max_batch_size = 8
    models.resolve_model.return_value = changed
    assert EmbeddingSpaceDescriptor.from_resolved(changed).digest == space.digest
    assert await service.embed(["fact"], scope=scope, expected_space=space) == first
    assert models.resolve_model.await_count == 2  # cached values still require current access
    create.assert_awaited_once()  # cache hits create no new physical charge
    guard.before_send.assert_awaited_once()
    await service.embed(["new fact"], scope=scope, expected_space=space)
    assert guard.before_send.await_count == 2
    assert guard.after_send.await_count == 2
    assert guard.before_send.await_args.args[0].model.input_price_per_million == 42
    models.resolve_model.side_effect = ForbiddenError("access revoked")
    with pytest.raises(ForbiddenError):
        await service.embed(["fact"], scope=scope, expected_space=space)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [True, "0.1", float("nan"), float("inf")])
async def test_real_adapter_rejects_malformed_components_before_coercion(monkeypatch, invalid):
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding

    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            embeddings=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        data=[SimpleNamespace(index=0, embedding=[invalid] + [0.1] * 1535)]
                    )
                )
            ),
            close=AsyncMock(),
        ),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.inference.embedding.create_ssrf_safe_async_client",
        lambda **kwargs: None,
    )
    adapter = OpenAICompatibleEmbedding(resolved_embedding())
    with pytest.raises(ServerRequestsError):
        await adapter.embed_batch(["fact"])
    await adapter.aclose()


def test_unverified_alias_cannot_satisfy_strict_space():
    resolved = resolved_embedding(model_name="embedding-2026-10-13")
    descriptor = EmbeddingSpaceDescriptor.from_resolved(resolved)
    assert descriptor.revision_assurance == "unverified_alias"
    assert descriptor.provider_revision is None
    with pytest.raises(EmbeddingRevisionUnverified):
        descriptor.require_verified()


def test_dispatch_snapshot_reuses_the_frozen_space_identity_without_secrets():
    from app.application.services.execution_usage_service import configuration_snapshot

    model = resolved_embedding()
    frozen = EmbeddingSpaceDescriptor.from_resolved(model)
    snapshot = configuration_snapshot(model, policy_revision="1", tool_fingerprint=None)
    assert snapshot["embedding_space_digest"] == frozen.digest
    assert model.credential not in str(snapshot)
    model.model.input_price_per_million = 42
    updated = configuration_snapshot(model, policy_revision="1", tool_fingerprint=None)
    assert updated["embedding_space_digest"] == frozen.digest
    assert updated["price_revision"] != snapshot["price_revision"]


@pytest.mark.asyncio
async def test_cache_isolates_scope_and_purpose():
    service, _, _, adapter, _ = _service(resolved_embedding())
    for scope, purpose in [
        (OwnerScope.personal("a"), "memory.query"),
        (OwnerScope.personal("b"), "memory.query"),
        (OwnerScope.personal("a"), "knowledge_base.query"),
    ]:
        await service.embed(["same"], scope=scope, purpose_context=purpose)
    assert adapter.embed_batch.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf"), True])
async def test_nonfinite_and_boolean_vectors_never_enter_cache(invalid):
    service, _, _, adapter, _ = _service(resolved_embedding())
    adapter.embed_batch.return_value = [[invalid] + [0.1] * 1535]
    with pytest.raises(ServerRequestsError):
        await service.embed(["fact"], scope=OwnerScope.personal("u"))
    assert not service._cache


@pytest.mark.parametrize("field", ["model_name", "endpoint_id", "extra_params"])
def test_semantic_changes_alter_space(field):
    original = resolved_embedding()
    changed = original.model_copy(deep=True)
    setattr(changed.model, field, {"task": "retrieval"} if field == "extra_params" else "changed")
    # A changed model endpoint must resolve to that endpoint before invocation.
    if field == "endpoint_id":
        changed.endpoint.id = "changed"
    assert (
        EmbeddingSpaceDescriptor.from_resolved(changed).digest
        != EmbeddingSpaceDescriptor.from_resolved(original).digest
    )
