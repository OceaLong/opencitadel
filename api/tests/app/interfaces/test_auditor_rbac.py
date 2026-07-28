#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Auditor RBAC: require_non_auditor blocks write operations."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.routing import iter_route_contexts

from app.application.errors.exceptions import ForbiddenError
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import (
    enforce_auditor_read_only,
    require_non_auditor,
    require_service_api_key,
)
from app.interfaces.endpoints.routes import create_api_routes


def _make_auditor_principal() -> Principal:
    return Principal(user_id="auditor-1", global_role=GlobalRole.AUDITOR, token_version=0)


@pytest.mark.asyncio
async def test_require_non_auditor_rejects_auditor():
    with patch(
        "app.interfaces.auth_dependencies.get_current_principal",
        new=AsyncMock(return_value=_make_auditor_principal()),
    ):
        with pytest.raises(ForbiddenError):
            await require_non_auditor()


@pytest.mark.asyncio
async def test_require_non_auditor_allows_user():
    user = Principal(user_id="user-1", global_role=GlobalRole.USER, token_version=0)
    with patch(
        "app.interfaces.auth_dependencies.get_current_principal",
        new=AsyncMock(return_value=user),
    ):
        principal = await require_non_auditor()
        assert principal.user_id == "user-1"


@pytest.mark.asyncio
async def test_authenticated_router_rejects_auditor_write_request():
    request = type("_Request", (), {"method": "POST"})()

    with pytest.raises(ForbiddenError):
        await enforce_auditor_read_only(
            request,
            principal=_make_auditor_principal(),
        )


@pytest.mark.asyncio
async def test_authenticated_router_allows_auditor_read_request():
    request = type("_Request", (), {"method": "GET"})()

    principal = await enforce_auditor_read_only(
        request,
        principal=_make_auditor_principal(),
    )

    assert principal.user_id == "auditor-1"


@pytest.mark.asyncio
async def test_service_api_key_cannot_bypass_auditor_read_only_role(monkeypatch):
    class _Repository:
        async def get_by_hash(self, _key_hash):
            return SimpleNamespace(owner_user_id="auditor-1")

    class _UserRepository:
        async def get_by_id(self, _user_id):
            return SimpleNamespace(
                id="auditor-1",
                global_role=GlobalRole.AUDITOR,
                token_version=0,
                is_active=True,
            )

    class _Uow:
        service_api_key = _Repository()
        user = _UserRepository()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(
        "app.interfaces.auth_dependencies.get_uow",
        lambda: _Uow(),
    )

    with pytest.raises(ForbiddenError):
        await require_service_api_key(x_api_key="sk-existing-auditor-key")


def test_marketplace_mutations_use_authenticated_read_only_boundary():
    router = create_api_routes()
    # FastAPI >=0.130 lazily wraps included sub-routers in `_IncludedRouter`
    # instead of eagerly flattening them into `router.routes`, so nested
    # routes (e.g. those registered via multiple levels of
    # `include_router`) no longer expose `.path` directly on
    # `router.routes`. `iter_route_contexts` walks the nesting and yields
    # the effective route contexts, which still expose `.path` and
    # `.dependant` like the original flattened `APIRoute` objects did.
    route_contexts = list(iter_route_contexts(router.routes))
    mutation = next(
        route
        for route in route_contexts
        if route.path == "/marketplace/assistant/route"
    )
    catalog = next(
        route
        for route in route_contexts
        if route.path == "/marketplace/apps"
    )

    assert any(
        dependency.call is enforce_auditor_read_only
        for dependency in mutation.dependant.dependencies
    )
    assert all(
        dependency.call is not enforce_auditor_read_only
        for dependency in catalog.dependant.dependencies
    )
