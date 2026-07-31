#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENV", "test")

from app.main import app
from app.domain.models.codebase import Codebase
from app.domain.models.codebase_version import CodebaseVersion, CodebaseVersionState
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType


@pytest.fixture(scope="session")
def _db_schema() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def client(_db_schema) -> TestClient:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Shared fake Unit-of-Work + factories for owner-scoped version-provider tests
# (KnowledgeVersionService / CodebaseVersionService, see task-19 brief).
# ---------------------------------------------------------------------------

FAKE_NOW = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)


class FakeUnitOfWork:
    """Generic async-context-manager UoW stub.

    Fake repositories are attached by keyword so the attribute name matches
    what ``IUnitOfWork`` exposes in production, e.g.::

        FakeUnitOfWork(knowledge_base=kb_repo, knowledge_version=version_repo)
        FakeUnitOfWork(codebase=cb_repo, codebase_version=version_repo)

    Pass ``exit_error=...`` to simulate a commit/rollback failure raised from
    ``__aexit__`` when the wrapped body did not itself raise.
    """

    def __init__(self, *, exit_error: Exception | None = None, **repos: Any) -> None:
        for name, repo in repos.items():
            setattr(self, name, repo)
        self.exit_error = exit_error
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> "FakeUnitOfWork":
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited += 1
        if exc_type is None and self.exit_error is not None:
            raise self.exit_error


def make_owner_scope(**overrides: Any) -> OwnerScope:
    """Owner scope factory; defaults to a personal scope for ``user-1``."""
    fields: dict[str, Any] = {
        "type": OwnerScopeType.PERSONAL,
        "user_id": "user-1",
        "team_id": None,
    }
    fields.update(overrides)
    return OwnerScope(**fields)


def make_kb_version(version_id: str = "v1", **overrides: Any) -> KnowledgeBaseVersion:
    """Knowledge-base version factory; defaults to a ready, published version."""
    offset = overrides.pop("offset", 0)
    published = overrides.pop("published", True)
    created_at = overrides.pop("created_at", FAKE_NOW + timedelta(minutes=offset))
    fields: dict[str, Any] = {
        "id": version_id,
        "knowledge_base_id": "kb-1",
        "state": KnowledgeVersionState.READY,
        "capabilities": {"keyword_search": True},
        "degraded_reasons": [],
        "created_at": created_at,
        "published_at": overrides.pop(
            "published_at", created_at if published else None
        ),
    }
    fields.update(overrides)
    return KnowledgeBaseVersion(**fields)


def make_codebase_version(version_id: str = "v1", **overrides: Any) -> CodebaseVersion:
    """Codebase version factory; defaults to a ready, published version."""
    offset = overrides.pop("offset", 0)
    published = overrides.pop("published", True)
    created_at = overrides.pop("created_at", FAKE_NOW + timedelta(minutes=offset))
    fields: dict[str, Any] = {
        "id": version_id,
        "codebase_id": "cb1",
        "state": CodebaseVersionState.READY,
        "capabilities": {"lexical_search": True, "vector_search": True},
        "degraded_reasons": [],
        "created_at": created_at,
        "published_at": overrides.pop(
            "published_at", created_at if published else None
        ),
    }
    fields.update(overrides)
    return CodebaseVersion(**fields)


class FakeKnowledgeBaseRepo:
    """Fake ``uow.knowledge_base`` repo: owner/team-scoped KB lookup."""

    def __init__(self, resources: dict[str, KnowledgeBase] | None = None) -> None:
        self.resources: dict[str, KnowledgeBase] = dict(resources or {})
        self.calls: list[tuple[str, OwnerScope]] = []

    async def get_kb(self, kb_id: str, scope: OwnerScope | None = None):
        assert scope is not None
        self.calls.append((kb_id, scope))
        kb = self.resources.get(kb_id)
        if kb is None:
            return None
        if scope.type is OwnerScopeType.TEAM:
            return kb if kb.team_id == scope.team_id else None
        return (
            kb
            if kb.owner_user_id == scope.user_id and kb.team_id is None
            else None
        )


class FakeKnowledgeVersionRepo:
    """Fake ``uow.knowledge_version`` repo backed by an in-memory dict."""

    def __init__(self, versions: list[KnowledgeBaseVersion] | None = None) -> None:
        self.versions = {item.id: item for item in (versions or [])}
        self.calls: list[tuple] = []

    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ):
        self.calls.append(("get", version_id, knowledge_base_id))
        version = self.versions.get(version_id)
        if version is None or version.knowledge_base_id != knowledge_base_id:
            return None
        return version

    async def list_versions(
        self,
        knowledge_base_id: str,
        *,
        limit: int = 500,
        before: tuple[datetime, str] | None = None,
    ):
        self.calls.append(("list", knowledge_base_id, limit, before))
        values = [
            item
            for item in self.versions.values()
            if item.knowledge_base_id == knowledge_base_id
            and (before is None or (item.created_at, item.id) < before)
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return values[:limit]


class FakeCodebaseRepo:
    """Fake ``uow.codebase`` repo: owner-scoped codebase lookup."""

    def __init__(self, resources: dict[str, Codebase] | None = None) -> None:
        self.resources: dict[str, Codebase] = dict(resources or {})
        self.calls: list[tuple[str, OwnerScope | None]] = []

    async def get_by_id(self, codebase_id: str, scope: OwnerScope | None = None):
        self.calls.append((codebase_id, scope))
        codebase = self.resources.get(codebase_id)
        if codebase and scope and codebase.owner_user_id != scope.user_id:
            return None
        return codebase


class FakeCodebaseVersionRepo:
    """Fake ``uow.codebase_version`` repo backed by an in-memory dict."""

    def __init__(self, versions: list[CodebaseVersion] | None = None) -> None:
        self.versions = {item.id: item for item in (versions or [])}
        self.calls: list[tuple] = []

    async def get_version(self, version_id: str, *, codebase_id: str | None = None):
        self.calls.append(("get", version_id, codebase_id))
        version = self.versions.get(version_id)
        if version and codebase_id and version.codebase_id != codebase_id:
            return None
        return version

    async def list_versions(
        self,
        codebase_id: str,
        *,
        limit: int = 500,
        before: tuple[datetime, str] | None = None,
    ):
        self.calls.append(("list", codebase_id, limit, before))
        values = [
            item
            for item in self.versions.values()
            if item.codebase_id == codebase_id
            and (before is None or (item.created_at, item.id) < before)
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return values[:limit]
