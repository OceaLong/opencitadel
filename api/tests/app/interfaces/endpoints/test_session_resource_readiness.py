"""Session resource routes keep ownership and immutable binding boundaries."""

import pytest

from app.domain.errors import NotFoundError
from app.domain.models.resource_bindings import ResourceKind, SessionResourceBinding
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints import resource_binding_routes
from app.interfaces.schemas.session import UpgradeResourceBindingRequest


def _ctx(user_id: str = "u1") -> WorkspaceContext:
    return WorkspaceContext(
        principal=Principal(user_id=user_id),
        scope=OwnerScope.personal(user_id),
    )


class _OwnerScopedBindingService:
    def __init__(self) -> None:
        self.binding = SessionResourceBinding(
            id="b1",
            session_id="s1",
            resource_kind=ResourceKind.CODEBASE,
            resource_id="cb1",
            version_id="cbv1",
            bound_by="u1",
        )
        self.scopes = []

    async def current_bindings(self, session_id, scope):
        self.scopes.append(("list", session_id, scope))
        if session_id != "s1" or scope.user_id != "u1":
            raise NotFoundError("session not found in owner scope")
        return [self.binding]

    async def current(self, session_id, kind, scope):
        self.scopes.append(("current", session_id, scope))
        if session_id != "s1" or scope.user_id != "u1":
            raise NotFoundError("session not found in owner scope")
        assert kind is ResourceKind.CODEBASE
        return self.binding

    async def upgrade(self, session_id, kind, target_version_id, *, actor_id, scope):
        self.scopes.append(("upgrade", session_id, scope, actor_id))
        if scope.user_id != "u1":
            raise NotFoundError("session not found in owner scope")
        assert (session_id, kind, target_version_id, actor_id) == (
            "s1",
            ResourceKind.CODEBASE,
            "cbv2",
            "u1",
        )
        return self.binding.model_copy(
            update={"id": "b2", "version_id": "cbv2", "supersedes_binding_id": "b1"}
        )


@pytest.mark.asyncio
async def test_binding_list_is_owner_scoped():
    """Catches a list endpoint leaking another user's session bindings."""
    service = _OwnerScopedBindingService()

    response = await resource_binding_routes.list_resource_bindings("s1", _ctx(), service)
    assert response.data[0].version_id == "cbv1"

    with pytest.raises(NotFoundError):
        await resource_binding_routes.list_resource_bindings("s1", _ctx("u2"), service)


@pytest.mark.asyncio
async def test_binding_upgrade_is_owner_scoped_and_reports_old_and_new_ids():
    """Catches an upgrade accepting a foreign owner or omitting history ids."""
    service = _OwnerScopedBindingService()
    response = await resource_binding_routes.upgrade_resource_binding(
        "s1",
        "codebase",
        UpgradeResourceBindingRequest(target_version_id="cbv2"),
        _ctx(),
        Principal(user_id="u1"),
        service,
    )
    assert response.data.old_binding_id == "b1"
    assert response.data.new_binding_id == "b2"
    assert response.data.current_version_id == "cbv2"

    with pytest.raises(NotFoundError):
        await resource_binding_routes.upgrade_resource_binding(
            "s1",
            "codebase",
            UpgradeResourceBindingRequest(target_version_id="cbv2"),
            _ctx("u2"),
            Principal(user_id="u2"),
            service,
        )
