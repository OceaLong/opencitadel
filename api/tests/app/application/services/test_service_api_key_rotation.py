"""Service API keys must be rotatable in place (parity with webhook secrets).

Before this, machine identities could only be deleted and recreated, changing
the key id every rotation and breaking references held by external callers.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.services.service_api_key_service import ServiceApiKeyService
from app.domain.errors import NotFoundError
from app.domain.models.service_api_key import ServiceApiKey


def _uow():
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    return uow


def _hasher():
    hasher = MagicMock()
    hasher.generate.return_value = SimpleNamespace(
        key_hash="new-hash", prefix="oc_new", plaintext="oc_new_secret"
    )
    return hasher


def test_rotate_swaps_material_and_returns_new_plaintext() -> None:
    uow = _uow()
    rotated_key = ServiceApiKey(
        id="key-1", owner_user_id="u1", name="ci", key_hash="new-hash", prefix="oc_new"
    )
    uow.service_api_key.rotate = AsyncMock(return_value=rotated_key)
    service = ServiceApiKeyService(lambda: uow, _hasher())

    result = asyncio.run(service.rotate_key(user_id="u1", key_id="key-1"))

    uow.service_api_key.rotate.assert_awaited_once_with(
        "key-1", "u1", key_hash="new-hash", prefix="oc_new"
    )
    uow.commit.assert_awaited_once()
    assert result.plaintext == "oc_new_secret"
    assert result.key.id == "key-1"


def test_rotate_missing_or_revoked_key_is_not_found() -> None:
    uow = _uow()
    uow.service_api_key.rotate = AsyncMock(return_value=None)
    service = ServiceApiKeyService(lambda: uow, _hasher())

    with pytest.raises(NotFoundError):
        asyncio.run(service.rotate_key(user_id="u1", key_id="missing"))

    uow.commit.assert_not_awaited()
