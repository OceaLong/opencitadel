from unittest.mock import AsyncMock

import pytest

from app.domain.errors import ForbiddenError
from app.domain.models.inference import InferenceBinding, InferencePurpose
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole
from app.interfaces.endpoints.inference_routes import delete_binding, set_binding
from app.interfaces.schemas.inference import (
    InferenceBindingRequest,
    InferenceBindingScope,
)


def _context(*, role: TeamRole | None = None, admin: bool = False) -> WorkspaceContext:
    principal = Principal(
        user_id="user-1",
        global_role=GlobalRole.ADMIN if admin else GlobalRole.USER,
        team_roles={"team-1": role} if role is not None else {},
    )
    scope = (
        OwnerScope.team("user-1", "team-1") if role is not None else OwnerScope.personal("user-1")
    )
    return WorkspaceContext(principal=principal, scope=scope)


@pytest.mark.asyncio
async def test_admin_can_create_global_binding() -> None:
    service = AsyncMock()
    service.set_binding.return_value = InferenceBinding(
        purpose=InferencePurpose.CHAT,
        model_id="model-1",
    )
    ctx = _context(admin=True)

    await set_binding(
        InferencePurpose.CHAT,
        InferenceBindingRequest(
            model_id="model-1",
            binding_scope=InferenceBindingScope.GLOBAL,
        ),
        ctx=ctx,
        _write_guard=ctx.principal,
        service=service,
    )

    service.set_binding.assert_awaited_once_with(
        InferencePurpose.CHAT,
        "model-1",
        scope=None,
    )


@pytest.mark.asyncio
async def test_non_admin_cannot_mutate_global_binding() -> None:
    ctx = _context()

    with pytest.raises(ForbiddenError, match="全局"):
        await set_binding(
            InferencePurpose.CHAT,
            InferenceBindingRequest(
                model_id="model-1",
                binding_scope=InferenceBindingScope.GLOBAL,
            ),
            ctx=ctx,
            _write_guard=ctx.principal,
            service=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_team_member_cannot_mutate_team_binding() -> None:
    ctx = _context(role=TeamRole.MEMBER)

    with pytest.raises(ForbiddenError, match="团队"):
        await delete_binding(
            InferencePurpose.CHAT,
            binding_scope=InferenceBindingScope.WORKSPACE,
            ctx=ctx,
            _write_guard=ctx.principal,
            service=AsyncMock(),
        )


@pytest.mark.asyncio
async def test_team_owner_can_delete_team_binding() -> None:
    service = AsyncMock()
    ctx = _context(role=TeamRole.OWNER)

    await delete_binding(
        InferencePurpose.CHAT,
        binding_scope=InferenceBindingScope.WORKSPACE,
        ctx=ctx,
        _write_guard=ctx.principal,
        service=service,
    )

    service.delete_binding.assert_awaited_once_with(
        InferencePurpose.CHAT,
        scope=ctx.scope,
    )
