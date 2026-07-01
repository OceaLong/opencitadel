#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.memory_service import MemoryService
from app.domain.models.memory_entry import MemoryEntry
from app.domain.models.scope import OwnerScope


class _FakeVectorService:
    async def store_embedding(self, *_args, **_kwargs):
        return None


class _FakeMemoryRepo:
    def __init__(self):
        self.saved = None
        self.owner_scope = None

    async def get_all(self, **kwargs):
        self.owner_scope = kwargs.get("owner_scope")
        return []

    async def save(self, entry):
        self.saved = entry


class _FakeUow:
    def __init__(self, repo):
        self.memory_entry = repo
        self.db_session = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_memory_create_and_list_use_owner_scope(monkeypatch):
    repo = _FakeMemoryRepo()
    service = MemoryService(lambda: _FakeUow(repo))
    owner_scope = OwnerScope.personal("user-1")
    monkeypatch.setattr(
        "app.application.services.vector_memory_service.get_vector_memory_service",
        lambda: _FakeVectorService(),
    )

    created = await service.create_entry(
        MemoryEntry(title="private", content="secret"),
        owner_scope=owner_scope,
    )
    await service.list_entries(owner_scope=owner_scope)

    assert created.owner_user_id == "user-1"
    assert created.team_id is None
    assert repo.saved.owner_user_id == "user-1"
    assert repo.owner_scope == owner_scope
