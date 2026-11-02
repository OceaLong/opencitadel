"""_freeze_embedding must reuse the caller's UoW, never open a nested one (P1-2)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.errors import ConflictError
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    ChatModelSettings,
)
from app.domain.models.resource_bindings import ResourceBuildIntent, ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import (
    ExecutionPolicy,
    KnowledgeBaseExecutionPolicy,
    initial_execution_policy,
)
from tests.app.embedding_test_support import resolved_embedding


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
    resolved = resolved_embedding()
    return SimpleNamespace(
        inference_binding=SimpleNamespace(
            get_effective_binding=AsyncMock(return_value=SimpleNamespace(model_id="model-1"))
        ),
        inference_model=SimpleNamespace(get_by_id=AsyncMock(return_value=resolved.model)),
        inference_endpoint=SimpleNamespace(get_by_id=AsyncMock(return_value=resolved.endpoint)),
        knowledge_base=SimpleNamespace(get_kb=AsyncMock(return_value=SimpleNamespace(settings={}))),
        knowledge_version=SimpleNamespace(freeze_embedding_space=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_freeze_embedding_reads_binding_via_caller_uow_without_resolve() -> None:
    inference_bindings = Mock()
    inference_bindings.resolve = AsyncMock()
    service = _service(inference_bindings)
    uow = _uow_with_embedding_binding()
    policy = ExecutionPolicy(knowledge_base=KnowledgeBaseExecutionPolicy(vector_enabled=True))

    frozen = await service._freeze_embedding(
        _build(), OwnerScope.personal("u-1"), policy=policy, uow=uow
    )

    assert frozen.embedding_model_id == "model-1"
    assert frozen.embedding_dimensions == PLATFORM_EMBEDDING_DIMENSIONS
    assert frozen.embedding_space_digest is not None
    assert (
        uow.knowledge_version.freeze_embedding_space.await_args.kwargs["space"].digest
        == frozen.embedding_space_digest
    )
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
    policy = initial_execution_policy()

    build = _build()
    frozen = await service._freeze_embedding(
        build, OwnerScope.personal("u-1"), policy=policy, uow=uow
    )

    assert frozen is build
    inference_bindings.resolve.assert_not_awaited()
    uow.inference_binding.get_effective_binding.assert_not_awaited()
    uow.inference_model.get_by_id.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_binding", ["missing", "chat"])
async def test_explicit_vector_enablement_validates_its_model_before_admission(invalid_binding):
    service = _service(Mock())
    uow = _uow_with_embedding_binding()
    if invalid_binding == "missing":
        uow.inference_binding.get_effective_binding.return_value = None
    else:
        uow.inference_model.get_by_id.return_value = SimpleNamespace(
            id="model-1", settings=ChatModelSettings()
        )
    policy = ExecutionPolicy(knowledge_base=KnowledgeBaseExecutionPolicy(vector_enabled=True))

    with pytest.raises(ConflictError):
        await service._freeze_embedding(
            _build(), OwnerScope.personal("u-1"), policy=policy, uow=uow
        )

    uow.inference_binding.get_effective_binding.assert_awaited_once()


@pytest.mark.asyncio
async def test_strict_space_policy_rejects_unverified_alias_before_freezing_candidate():
    from app.domain.models.embedding_space import EmbeddingRevisionUnverified

    service = _service(Mock())
    uow = _uow_with_embedding_binding()
    uow.knowledge_base.get_kb.return_value.settings = {"embedding_revision_policy": "verified_only"}
    policy = ExecutionPolicy(knowledge_base=KnowledgeBaseExecutionPolicy(vector_enabled=True))
    with pytest.raises(EmbeddingRevisionUnverified):
        await service._freeze_embedding(_build(), OwnerScope.personal("u"), policy=policy, uow=uow)
    uow.knowledge_version.freeze_embedding_space.assert_not_awaited()
