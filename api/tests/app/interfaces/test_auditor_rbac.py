#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Auditor RBAC: require_non_auditor blocks write operations."""
import pytest
from unittest.mock import AsyncMock, patch

from app.application.errors.exceptions import ForbiddenError
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import require_non_auditor


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
