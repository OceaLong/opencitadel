from types import SimpleNamespace

import pytest

from app.application.services.memory_service import MemoryService
from app.domain.models.memory_entry import MemoryEntry
from app.domain.models.scope import OwnerScope
from app.domain.runtime_policy import MemoryExecutionPolicy
from tests.app.embedding_test_support import embedding_space


class _FakeMemoryRepo:
    def __init__(self):
        self.saved = None
        self.owner_scope = None
        self.last_limit = None

    async def get_all(self, **kwargs):
        self.owner_scope = kwargs.get("owner_scope")
        return []

    async def save(self, entry, **kwargs):
        self.saved = entry

    async def update_embedding(self, entry_id, embedding, *, embedding_space_digest):
        self.saved.embedding_space_digest = embedding_space_digest

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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "recall"])
async def test_memory_embedding_never_borrows_open_business_transaction(operation):
    from unittest.mock import AsyncMock

    active = 0
    repo = _FakeMemoryRepo()
    repo.get_by_id = AsyncMock(return_value=MemoryEntry(id="entry-1"))
    repo.update_embedding = AsyncMock()
    repo.vector_search_entries = AsyncMock(return_value=[])

    class TrackingUow(_FakeUow):
        async def __aenter__(self):
            nonlocal active
            active += 1
            return self

        async def __aexit__(self, *args):
            nonlocal active
            active -= 1
            return False

    async def embed(*args, **kwargs):
        assert active == 0, "provider send holds caller transaction / pool connection"
        return [[0.1] * 1536]

    service = MemoryService(
        lambda: TrackingUow(repo),
        SimpleNamespace(embed=embed, resolve_space=AsyncMock(return_value=embedding_space())),
    )
    scope = OwnerScope.personal("user-1")
    policy = MemoryExecutionPolicy(vector_enabled=True)
    if operation == "recall":
        await service.recall_for_session("session-1", owner_scope=scope, policy=policy)
    elif operation == "update":
        await service.update_entry("entry-1", MemoryEntry(content="new"), scope, policy=policy)
    else:
        await service.create_entry(MemoryEntry(content="new"), scope, policy=policy)
    assert active == 0


@pytest.mark.asyncio
async def test_memory_update_revalidates_entry_after_embedding_before_save():
    from unittest.mock import AsyncMock

    from app.domain.errors import NotFoundError

    repo = _FakeMemoryRepo()
    repo.get_by_id = AsyncMock(side_effect=[MemoryEntry(id="entry-1"), None])
    service = MemoryService(
        lambda: _FakeUow(repo),
        SimpleNamespace(
            embed=AsyncMock(return_value=[[0.1] * 1536]),
            resolve_space=AsyncMock(return_value=embedding_space()),
        ),
    )
    with pytest.raises(NotFoundError):
        await service.update_entry(
            "entry-1",
            MemoryEntry(content="new"),
            OwnerScope.personal("user-1"),
            policy=MemoryExecutionPolicy(vector_enabled=True),
        )
    assert repo.saved is None


@pytest.mark.asyncio
async def test_context_recall_uses_accepted_query_and_records_exact_revisions():
    from unittest.mock import AsyncMock

    from app.domain.execution.context_models import context_digest
    from app.domain.execution.context_resources import memory_content_digest

    entry = MemoryEntry(
        id="memory-1",
        owner_user_id="user-1",
        title="Order",
        content="Do not renew ORD83",
        embedding_space_digest=embedding_space().digest,
    )
    repo = _FakeMemoryRepo()
    repo.recall_for_session = AsyncMock(return_value=[entry])
    repo.vector_search_entries = AsyncMock(return_value=[entry])
    repo.touch_used = AsyncMock()
    embeddings = SimpleNamespace(
        embed=AsyncMock(return_value=[[0.1] * 1536]),
        resolve_space=AsyncMock(return_value=embedding_space()),
    )
    service = MemoryService(lambda: _FakeUow(repo), embeddings)
    recall = await service.recall_context(
        "session-1",
        query="accepted ORD83 question",
        owner_scope=OwnerScope.personal("user-1"),
        policy=MemoryExecutionPolicy(vector_enabled=True),
    )
    assert embeddings.embed.call_args.args[0] == ["accepted ORD83 question"]
    assert recall.query_digest == context_digest("accepted ORD83 question")
    assert recall.query_embedding_space_digest == embedding_space().digest
    assert recall.entries[0].content_digest == memory_content_digest(entry)
    assert recall.entries[0].embedding_space_digest == embedding_space().digest
    assert "Do not renew ORD83" in recall.content
    assert "content" not in recall.evidence().model_dump()
    # Activity counters may advance while the immutable source version remains valid.
    entry.use_count += 1
    assert recall.entries[0].content_digest == memory_content_digest(entry)
    entry.content = "renew ORD83"
    assert recall.entries[0].content_digest != memory_content_digest(entry)
