#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Auditor RBAC: require_non_auditor blocks write operations."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.errors import ForbiddenError
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import (
    enforce_auditor_read_only,
    require_non_auditor,
    require_service_api_key,
)


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
