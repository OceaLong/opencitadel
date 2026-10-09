from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.services.inference_model_service import InferenceModelService
from app.domain.errors import ConflictError, NotFoundError
from app.domain.models.inference import InferenceBinding, InferencePurpose
from app.domain.models.scope import OwnerScope
from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit", [False, True])
async def test_chat_resolution_borrows_locked_caller_without_opening_another_uow(explicit):
    resolved = resolved_chat_model()
    scope = OwnerScope.personal("owner")
    unit = SimpleNamespace(
        inference_binding=SimpleNamespace(
            get_effective_binding=AsyncMock(
                return_value=InferenceBinding(
                    purpose=InferencePurpose.CHAT, model_id=resolved.model.id
                )
            )
        ),
        inference_model=SimpleNamespace(get_by_id=AsyncMock(return_value=resolved.model)),
        inference_endpoint=SimpleNamespace(get_by_id=AsyncMock(return_value=resolved.endpoint)),
    )

    def forbidden_factory():
        raise AssertionError("nested UoW must not be opened")

    service = InferenceModelService(
        forbidden_factory, SimpleNamespace(credential_required=lambda _: True), None, None
    )
    result = await service.resolve_chat(
        resolved.model.id if explicit else None, scope=scope, uow=unit
    )
    assert result.model.id == resolved.model.id
    unit.inference_model.get_by_id.assert_awaited_once_with(resolved.model.id, scope=scope)
    unit.inference_endpoint.get_by_id.assert_awaited_once_with(
        resolved.model.endpoint_id, scope=scope
    )
    unit.inference_endpoint.get_by_id.return_value = None
    with pytest.raises(ConflictError):
        await service.resolve_chat(resolved.model.id if explicit else None, scope=scope, uow=unit)
    unit.inference_model.get_by_id.return_value = None
    with pytest.raises((ConflictError, NotFoundError)):
        await service.resolve_chat(resolved.model.id if explicit else None, scope=scope, uow=unit)
