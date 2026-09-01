"""_freeze_embedding must reuse the caller's UoW, never open a nested one (P1-2)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    EmbeddingModelSettings,
)
from app.domain.models.resource_bindings import ResourceBuildIntent, ResourceKind
from app.domain.models.scope import OwnerScope


def _service(inference_bindings) -> KnowledgeBaseService:
    def _no_uow():
        raise AssertionError("_freeze_embedding must not open a new UnitOfWork")

    return KnowledgeBaseService(
        _no_uow,
        file_storage=Mock(),
        run_admission_service=Mock(),
        run_control_service=Mock(),
        run_projection=Mock(),
        web_documents=Mock(),
        version_builder=Mock(),
        inference_bindings=inference_bindings,
    )


def _build() -> ResourceBuildIntent:
    return ResourceBuildIntent(
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb-1",
        version_id="v-1",
    )


def _uow_with_embedding_binding() -> SimpleNamespace:
    return SimpleNamespace(
        inference_binding=SimpleNamespace(
            get_effective_binding=AsyncMock(return_value=SimpleNamespace(model_id="model-1"))
        ),
        inference_model=SimpleNamespace(
            get_by_id=AsyncMock(
                return_value=SimpleNamespace(id="model-1", settings=EmbeddingModelSettings())
            )
        ),
    )


@pytest.mark.asyncio
async def test_freeze_embedding_reads_binding_via_caller_uow_without_resolve() -> None:
    inference_bindings = Mock()
    inference_bindings.resolve = AsyncMock()
    service = _service(inference_bindings)
    uow = _uow_with_embedding_binding()
    policy = SimpleNamespace(knowledge_base=SimpleNamespace(vector_enabled=True))

    frozen = await service._freeze_embedding(
        _build(), OwnerScope.personal("u-1"), policy=policy, uow=uow
    )

    assert frozen.embedding_model_id == "model-1"
    assert frozen.embedding_dimensions == PLATFORM_EMBEDDING_DIMENSIONS
    # The binding is resolved on the caller's connection, never through
    # InferenceBindingService.resolve (which would open a second UoW).
    inference_bindings.resolve.assert_not_awaited()
    uow.inference_binding.get_effective_binding.assert_awaited_once()
    uow.inference_model.get_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_freeze_embedding_noop_when_vector_disabled_touches_no_uow() -> None:
    inference_bindings = Mock()
    inference_bindings.resolve = AsyncMock()
    service = _service(inference_bindings)
    uow = _uow_with_embedding_binding()
    policy = SimpleNamespace(knowledge_base=SimpleNamespace(vector_enabled=False))

    build = _build()
    frozen = await service._freeze_embedding(
        build, OwnerScope.personal("u-1"), policy=policy, uow=uow
    )

    assert frozen is build
    inference_bindings.resolve.assert_not_awaited()
    uow.inference_binding.get_effective_binding.assert_not_awaited()
