import os
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError

os.environ.setdefault("ENV", "test")

from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.main import create_app
from core.config import DeploymentSettings

app = create_app(DeploymentSettings(env="test"))

_POSTGRES_SETTING_FIELDS = {
    "postgres_host",
    "postgres_user",
    "postgres_password",
    "postgres_db",
    "sqlalchemy_database_uri",
}
_REDIS_SETTING_FIELDS = {
    "redis_host",
    "redis_port",
    "redis_db",
    "redis_password",
}


@pytest.fixture(scope="session")
def _postgres_available() -> None:
    from core.config import load_deployment_settings, sqlalchemy_sync_database_uri

    settings = load_deployment_settings()
    try:
        engine = create_engine(
            sqlalchemy_sync_database_uri(settings),
            connect_args={"connect_timeout": 2},
            pool_pre_ping=True,
        )
        with engine.connect():
            pass
    except SQLAlchemyError as exc:
        strict = os.getenv("OPENCITADEL_REQUIRE_POSTGRES_TESTS") == "1"
        explicitly_configured = bool(
            _POSTGRES_SETTING_FIELDS.intersection(settings.model_fields_set)
        )
        message = f"PostgreSQL test database is unavailable: {exc}"
        if strict or explicitly_configured:
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    finally:
        if "engine" in locals():
            engine.dispose()


@pytest.fixture(scope="session")
def _db_schema(_postgres_available) -> None:
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(scope="session")
def postgres_integration(_db_schema) -> None:
    """Canonical PostgreSQL proof gate with fail-closed CI availability."""


@pytest.fixture(scope="session")
def _redis_available() -> None:
    from core.config import load_deployment_settings

    settings = load_deployment_settings()
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        password=settings.redis_password,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        client.ping()
    except (OSError, RedisError) as exc:
        strict = os.getenv("OPENCITADEL_REQUIRE_REDIS_TESTS") == "1"
        explicitly_configured = bool(_REDIS_SETTING_FIELDS.intersection(settings.model_fields_set))
        message = f"Redis test service is unavailable: {exc}"
        if strict or explicitly_configured:
            pytest.fail(message, pytrace=False)
        pytest.skip(message)
    finally:
        client.close()


@pytest.fixture(scope="session")
def redis_integration(_redis_available) -> None:
    """Canonical Redis proof gate with fail-closed CI availability."""


@pytest.fixture(scope="session")
def client(_db_schema) -> TestClient:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Shared fake Unit-of-Work + factories for owner-scoped version-provider tests.
# ---------------------------------------------------------------------------

FAKE_NOW = datetime(2026, 7, 29, 2, 0, tzinfo=UTC)


class FakeUnitOfWork:
    """Generic async-context-manager UoW stub.

    Fake repositories are attached by keyword so the attribute name matches
    what ``IUnitOfWork`` exposes in production, e.g.::

        FakeUnitOfWork(knowledge_base=kb_repo, knowledge_version=version_repo)

    Pass ``exit_error=...`` to simulate an explicit commit failure. The name is
    retained only in test data while production callers migrate atomically.
    """

    def __init__(self, *, exit_error: Exception | None = None, **repos: Any) -> None:
        for name, repo in repos.items():
            setattr(self, name, repo)
        self.exit_error = exit_error
        self.entered = 0
        self.exited = 0
        self.commits = 0
        self.rollbacks = 0
        self._committed = False
        self._rolled_back = False

    async def __aenter__(self) -> Self:
        self.entered += 1
        self._committed = False
        self._rolled_back = False
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.exited += 1
        if not self._committed and not self._rolled_back:
            await self.rollback()

    async def commit(self) -> None:
        self.commits += 1
        if self.exit_error is not None:
            raise self.exit_error
        self._committed = True

    async def rollback(self) -> None:
        if self._rolled_back:
            return
        self.rollbacks += 1
        self._rolled_back = True


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
        "published_at": overrides.pop("published_at", created_at if published else None),
    }
    fields.update(overrides)
    return KnowledgeBaseVersion(**fields)


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
        return kb if kb.owner_user_id == scope.user_id and kb.team_id is None else None


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
