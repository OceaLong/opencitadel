#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.errors.exceptions import BadRequestError
from app.domain.models.llm_model import LLMModel, ResourceVisibility
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole
from app.interfaces.endpoints.llm_model_routes import (
    _to_response,
    create_model,
    delete_model,
    probe_multimodal,
    set_preferred_model,
    update_model,
)
from app.interfaces.schemas.llm_model import (
    LLMModelCreateRequest,
    LLMModelUpdateRequest,
)


def _global_model() -> LLMModel:
    return LLMModel(
        id="model-1",
        endpoint_id="endpoint-1",
        display_name="Global Model",
        model_name="gpt-test",
        visibility=ResourceVisibility.GLOBAL,
    )


def test_model_response_includes_team_id():
    response = _to_response(
        _global_model().model_copy(
            update={
                "visibility": ResourceVisibility.PRIVATE,
                "owner_user_id": "creator-1",
                "team_id": "team-1",
            }
        )
    )

    assert response.team_id == "team-1"


def _admin_context() -> WorkspaceContext:
    return WorkspaceContext(
        principal=Principal(
            user_id="admin-1",
            global_role=GlobalRole.ADMIN,
        ),
        scope=OwnerScope.personal("admin-1"),
    )


class _AdminCapabilityRecordingService:
    def __init__(self):
        self.model = _global_model()
        self.calls = {}

    async def get_model(self, model_id, mask=True, scope=None):
        return self.model

    async def create_model(
        self,
        model,
        scope=None,
        *,
        allow_global_mutation=False,
    ):
        self.calls["create"] = allow_global_mutation
        return model

    async def update_model(
        self,
        model_id,
        updates,
        scope=None,
        *,
        allow_global_mutation=False,
    ):
        self.calls["update"] = allow_global_mutation
        return updates

    async def delete_model(
        self,
        model_id,
        scope=None,
        *,
        allow_global_mutation=False,
    ):
        self.calls["delete"] = allow_global_mutation

    async def probe_multimodal(
        self,
        model_id,
        *,
        scope,
        allow_global_mutation=False,
    ):
        self.calls["probe"] = allow_global_mutation
        return {"status": "ok", "message": "checked"}

    async def set_preference(self, model_id, *, scope):
        self.calls["preference"] = scope
        return self.model.model_copy(update={"is_default": True})


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_admin_create_route_grants_global_mutation_capability():
    service = _AdminCapabilityRecordingService()

    await create_model(
        LLMModelCreateRequest(
            endpoint_id="endpoint-1",
            display_name="Global Model",
            model_name="gpt-test",
        ),
        ctx=_admin_context(),
        llm_model_service=service,
    )

    assert service.calls["create"] is True


@pytest.mark.anyio
async def test_admin_update_route_grants_global_mutation_capability():
    service = _AdminCapabilityRecordingService()

    await update_model(
        "model-1",
        LLMModelUpdateRequest(display_name="Changed"),
        ctx=_admin_context(),
        llm_model_service=service,
    )

    assert service.calls["update"] is True


@pytest.mark.anyio
async def test_admin_delete_route_grants_global_mutation_capability():
    service = _AdminCapabilityRecordingService()

    await delete_model(
        "model-1",
        ctx=_admin_context(),
        llm_model_service=service,
    )

    assert service.calls["delete"] is True


@pytest.mark.anyio
async def test_admin_probe_route_grants_global_mutation_capability():
    service = _AdminCapabilityRecordingService()

    await probe_multimodal(
        "model-1",
        ctx=_admin_context(),
        llm_model_service=service,
    )

    assert service.calls["probe"] is True


@pytest.mark.anyio
async def test_create_route_rejects_generic_default_control():
    service = _AdminCapabilityRecordingService()

    with pytest.raises(BadRequestError, match="专用接口"):
        await create_model(
            LLMModelCreateRequest(
                endpoint_id="endpoint-1",
                display_name="Global Model",
                model_name="gpt-test",
                is_default=True,
            ),
            ctx=_admin_context(),
            llm_model_service=service,
        )

    assert "create" not in service.calls


@pytest.mark.anyio
async def test_update_route_rejects_generic_default_control():
    service = _AdminCapabilityRecordingService()

    with pytest.raises(BadRequestError, match="专用接口"):
        await update_model(
            "model-1",
            LLMModelUpdateRequest(is_default=False),
            ctx=_admin_context(),
            llm_model_service=service,
        )

    assert "update" not in service.calls


@pytest.mark.anyio
async def test_team_admin_can_set_workspace_model_preference():
    service = _AdminCapabilityRecordingService()
    ctx = WorkspaceContext(
        principal=Principal(
            user_id="team-admin",
            team_roles={"team-1": TeamRole.ADMIN},
        ),
        scope=OwnerScope.team("team-admin", "team-1"),
    )

    await set_preferred_model(
        "model-1",
        ctx=ctx,
        _write_guard=ctx.principal,
        llm_model_service=service,
    )

    assert service.calls["preference"] == ctx.scope


@pytest.mark.anyio
async def test_team_member_cannot_change_team_model_preference():
    service = _AdminCapabilityRecordingService()
    ctx = WorkspaceContext(
        principal=Principal(
            user_id="member-1",
            team_roles={"team-1": TeamRole.MEMBER},
        ),
        scope=OwnerScope.team("member-1", "team-1"),
    )

    from app.application.errors.exceptions import ForbiddenError

    with pytest.raises(ForbiddenError, match="团队"):
        await set_preferred_model(
            "model-1",
            ctx=ctx,
            _write_guard=ctx.principal,
            llm_model_service=service,
        )

    assert "preference" not in service.calls
