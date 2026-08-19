"""Snapshot endpoint contract tests."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.interfaces.endpoints import codebase_routes


def _ctx() -> WorkspaceContext:
    return WorkspaceContext(principal=Principal(user_id="u1"), scope=OwnerScope.personal("u1"))


class _SnapshotService:
    def __init__(self):
        self.codebase = SimpleNamespace(snapshot_key="stored.tgz", updated_at=datetime(2026, 1, 1))
        self.package_calls = 0

    async def get_codebase(self, codebase_id, *, scope):
        assert (codebase_id, scope.user_id) == ("cb1", "u1")
        return self.codebase

    async def package_download(self, codebase_id, storage, *, scope):
        self.package_calls += 1
        self.codebase.snapshot_key = "new.tgz"
        return self.codebase.snapshot_key


@pytest.mark.asyncio
async def test_post_snapshot_is_the_only_snapshot_mutation():
    """Catches POST snapshots becoming a no-op."""
    service = _SnapshotService()
    response = await codebase_routes.create_codebase_snapshot("cb1", _ctx(), Principal(user_id="u1"), service, object())
    assert response.data.snapshot_key == "new.tgz"
    assert service.package_calls == 1
