#!/usr/bin/env python
# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pytest

from app.application.services.bootstrap_service import bootstrap_admin_user
from app.domain.models.user import GlobalRole, User
from app.infrastructure.security.password_hasher import PasswordHasher


class InMemoryUserRepo:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users: dict[str, User] = {user.id: user for user in (users or [])}

    async def get_by_id(self, user_id: str):
        return self.users.get(user_id)

    async def get_by_email(self, email: str):
        normalized = email.lower()
        for user in self.users.values():
            if user.email.lower() == normalized:
                return user
        return None

    async def get_by_username(self, username: str):
        for user in self.users.values():
            if user.username == username:
                return user
        return None

    async def list(self, limit: int = 100, offset: int = 0):
        return list(self.users.values())[:limit]

    async def save(self, user: User) -> None:
        self.users[user.id] = user

    async def delete_by_id(self, user_id: str) -> None:
        self.users.pop(user_id, None)


class FakeUow:
    def __init__(self, user_repo: InMemoryUserRepo) -> None:
        self.user = user_repo

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings(monkeypatch):
    config = SimpleNamespace(
        bootstrap_admin_email="admin",
        bootstrap_admin_password="admin",
    )
    monkeypatch.setattr(
        "app.application.services.bootstrap_service.get_settings",
        lambda: config,
    )
    return config


@pytest.mark.anyio
async def test_bootstrap_admin_backfills_missing_password(settings):
    admin = User(
        email="admin",
        username="admin",
        password_hash="",
        display_name="Administrator",
        global_role=GlobalRole.ADMIN,
    )
    repo = InMemoryUserRepo([admin])
    hasher = PasswordHasher()

    await bootstrap_admin_user(lambda: FakeUow(repo))

    saved = await repo.get_by_email("admin")
    assert saved is not None
    assert saved.password_hash
    assert hasher.verify("admin", saved.password_hash)


@pytest.mark.anyio
async def test_bootstrap_admin_does_not_overwrite_existing_password(settings):
    hasher = PasswordHasher()
    admin = User(
        email="admin",
        username="admin",
        password_hash=hasher.hash("existing-password"),
        display_name="Administrator",
        global_role=GlobalRole.ADMIN,
    )
    repo = InMemoryUserRepo([admin])
    original_hash = admin.password_hash

    await bootstrap_admin_user(lambda: FakeUow(repo))

    saved = await repo.get_by_email("admin")
    assert saved is not None
    assert saved.password_hash == original_hash
    assert hasher.verify("existing-password", saved.password_hash)
    assert not hasher.verify("admin", saved.password_hash)


@pytest.mark.anyio
async def test_bootstrap_admin_creates_user_on_empty_database(settings):
    repo = InMemoryUserRepo()
    hasher = PasswordHasher()

    await bootstrap_admin_user(lambda: FakeUow(repo))

    saved = await repo.get_by_email("admin")
    assert saved is not None
    assert saved.username == "admin"
    assert saved.global_role == GlobalRole.ADMIN
    assert saved.password_hash
    assert hasher.verify("admin", saved.password_hash)
