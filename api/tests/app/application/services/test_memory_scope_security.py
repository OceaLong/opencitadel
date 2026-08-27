from types import SimpleNamespace

import pytest

from app.application.services.memory_service import MemoryService
from app.domain.models.memory_entry import MemoryEntry
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import MemoryExecutionPolicy


class _FakeMemoryRepo:
    def __init__(self):
        self.saved = None
        self.owner_scope = None
        self.last_limit = None

    async def get_all(self, **kwargs):
        self.owner_scope = kwargs.get("owner_scope")
        return []

    async def save(self, entry):
        self.saved = entry

    async def recall_for_session(self, session_id, limit):
        assert session_id == "session-1"
        self.last_limit = limit
        return []


class _FakeUow:
    def __init__(self, repo):
        self.memory_entry = repo
        self.session = SimpleNamespace(
            get_by_id=self.get_session,
        )
        self.db_session = None

    async def get_session(self, session_id, scope=None):
        assert session_id == "session-1"
        return SimpleNamespace(latest_message="remember this")

    async def __aenter__(self):
        return self

    async def commit(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_memory_create_and_list_use_owner_scope():
    repo = _FakeMemoryRepo()
    service = MemoryService(lambda: _FakeUow(repo))
    owner_scope = OwnerScope.personal("user-1")
    created = await service.create_entry(
        MemoryEntry(title="private", content="secret"),
        owner_scope=owner_scope,
        policy=MemoryExecutionPolicy(vector_enabled=False),
    )
    await service.list_entries(owner_scope=owner_scope)

    assert created.owner_user_id == "user-1"
    assert created.team_id is None
    assert repo.saved.owner_user_id == "user-1"
    assert repo.owner_scope == owner_scope


@pytest.mark.asyncio
async def test_memory_recall_limit_comes_from_explicit_run_policy():
    repo = _FakeMemoryRepo()
    service = MemoryService(lambda: _FakeUow(repo))

    result = await service.recall_for_session(
        "session-1",
        owner_scope=OwnerScope.personal("user-1"),
        policy=MemoryExecutionPolicy(recall_limit=7, vector_enabled=False),
    )

    assert result == ""
    assert repo.last_limit == 7
