"""Current authority and bounded safe environment read projection (no database)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.application.evaluation.batch_service import BatchService
from app.domain.models.scope import OwnerScope, Principal


def setup_service():
    scope = OwnerScope.personal("user")
    principal = Principal(user_id="user", global_role="user", token_version=1)
    repo = SimpleNamespace(get=AsyncMock(), environment_statuses=AsyncMock(return_value=[]))
    authorize = AsyncMock()

    @asynccontextmanager
    async def factory(auth):
        assert auth.scope == scope
        yield SimpleNamespace(
            evaluation_dataset=SimpleNamespace(authorize=authorize), evaluation_batch=repo
        )

    service = BatchService(
        SimpleNamespace(uow_factory=factory, cursor_secret=b"a" * 32), preflight_factory=None
    )
    return service, scope, principal, repo, authorize


def lease(identity, state="quarantine"):
    return {
        "id": identity,
        "environment_version": uuid4(),
        "case_id": uuid4(),
        "config_version": uuid4(),
        "repeat": 1,
        "generation": 1,
        "revision": 5,
        "state": state,
        "prior_failed_operations": {"reset": 1},
        "namespace": "private",
        "error": "secret",
    }


@pytest.mark.asyncio
async def test_current_quarantine_and_repaired_history_are_safe_and_paginated():
    service, scope, principal, repo, authorize = setup_service()
    batch = uuid4()
    first, second = UUID(int=1), UUID(int=2)
    repo.environment_statuses.return_value = [lease(first), lease(second, "verified_clean")]
    page = await service.environments(scope, principal, batch, limit=1)
    authorize.assert_awaited_once_with(scope, principal, write=False)
    repo.get.assert_awaited_once_with(scope, batch)
    item = page["items"][0].model_dump(mode="json")
    assert item["reusable"] is False
    assert item["state"] == "quarantine"
    assert item["prior_failed_operations"] == {"reset": 1}
    assert not {"namespace", "error", "requester", "resources", "actual_versions"} & item.keys()
    repo.environment_statuses.return_value = [lease(second, "verified_clean")]
    next_page = await service.environments(
        scope, principal, batch, cursor=page["next_cursor"], limit=1
    )
    assert next_page["items"][0].reusable is True
    assert next_page["items"][0].prior_failed_operations == {"reset": 1}
    repo.environment_statuses.assert_awaited_with(scope, batch, after=first, limit=2)
    with pytest.raises(ValueError, match="invalid_cursor"):
        await service.environments(scope, principal, uuid4(), cursor=page["next_cursor"])


@pytest.mark.asyncio
async def test_current_authority_and_foreign_batch_checked_before_lease_read():
    service, scope, principal, repo, authorize = setup_service()
    authorize.side_effect = PermissionError("membership_revoked")
    with pytest.raises(PermissionError):
        await service.environments(scope, principal, uuid4())
    repo.environment_statuses.assert_not_awaited()
    authorize.side_effect = None
    repo.get.side_effect = ValueError("batch_not_found")
    with pytest.raises(ValueError, match="batch_not_found"):
        await service.environments(scope, principal, uuid4())
    repo.environment_statuses.assert_not_awaited()
