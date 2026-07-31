#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Owner-scoped provider contract for immutable knowledge versions."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.container import BaseContainer
from app.application.errors.exceptions import BadRequestError, NotFoundError
from app.application.services.knowledge_version_service import (
    KnowledgeVersionService,
)
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import BuildState, ResourceKind
from app.domain.models.scope import OwnerScope
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork


NOW = datetime(2026, 7, 29, 2, 0, tzinfo=timezone.utc)


def _version(
    version_id: str,
    *,
    kb_id: str = "kb-1",
    state: KnowledgeVersionState = KnowledgeVersionState.READY,
    published: bool = True,
    offset: int = 0,
    capabilities: dict[str, bool] | None = None,
    reasons: list[str] | None = None,
) -> KnowledgeBaseVersion:
    return KnowledgeBaseVersion(
        id=version_id,
        knowledge_base_id=kb_id,
        state=state,
        capabilities=capabilities or {"keyword_search": True},
        degraded_reasons=reasons or [],
        created_at=NOW + timedelta(minutes=offset),
        published_at=(NOW + timedelta(minutes=offset)) if published else None,
    )


class _VersionRepo:
    def __init__(self, versions: list[KnowledgeBaseVersion]) -> None:
        self.versions = {item.id: item for item in versions}
        self.calls: list[tuple] = []

    async def get_version(
        self,
        version_id: str,
        *,
        knowledge_base_id: str,
    ):
        self.calls.append(("get", version_id, knowledge_base_id))
        version = self.versions.get(version_id)
        if version is None:
            return None
        if version.knowledge_base_id != knowledge_base_id:
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
            and (
                before is None
                or (item.created_at, item.id) < before
            )
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return values[:limit]


class _KnowledgeRepo:
    def __init__(self, resources: dict[str, KnowledgeBase]) -> None:
        self.resources = resources
        self.calls: list[tuple[str, OwnerScope]] = []

    async def get_kb(self, kb_id: str, scope: OwnerScope | None = None):
        assert scope is not None
        self.calls.append((kb_id, scope))
        kb = self.resources.get(kb_id)
        if kb is None:
            return None
        if scope.type.value == "team":
            return kb if kb.team_id == scope.team_id else None
        return (
            kb
            if kb.owner_user_id == scope.user_id and kb.team_id is None
            else None
        )


class _Uow:
    def __init__(
        self,
        knowledge: _KnowledgeRepo,
        versions: _VersionRepo,
        *,
        exit_error: Exception | None = None,
    ) -> None:
        self.knowledge_base = knowledge
        self.knowledge_version = versions
        self.exit_error = exit_error
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exited += 1
        if exc_type is None and self.exit_error is not None:
            raise self.exit_error


def _service(
    *,
    kb: KnowledgeBase,
    versions: list[KnowledgeBaseVersion],
    exit_error: Exception | None = None,
):
    knowledge_repo = _KnowledgeRepo({kb.id: kb})
    version_repo = _VersionRepo(versions)
    created: list[_Uow] = []

    def factory():
        uow = _Uow(knowledge_repo, version_repo, exit_error=exit_error)
        created.append(uow)
        return uow

    return (
        KnowledgeVersionService(uow_factory=factory),
        knowledge_repo,
        version_repo,
        created,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kb", "scope"),
    (
        (
            KnowledgeBase(
                id="kb-1",
                owner_user_id="user-1",
                active_version_id="v2",
            ),
            OwnerScope.personal("user-1"),
        ),
        (
            KnowledgeBase(
                id="kb-1",
                team_id="team-1",
                active_version_id="v2",
            ),
            OwnerScope.team("member-1", "team-1"),
        ),
    ),
)
async def test_defaults_to_owner_scoped_active_version_in_one_uow(kb, scope):
    service, knowledge_repo, version_repo, uows = _service(
        kb=kb,
        versions=[_version("v2")],
    )

    resolved = await service.resolve_published_version("kb-1", None, scope)

    assert resolved.resource_kind is ResourceKind.KNOWLEDGE_BASE
    assert resolved.resource_id == "kb-1"
    assert resolved.version_id == "v2"
    assert resolved.state is BuildState.SUCCEEDED
    assert resolved.published is True
    assert resolved.degraded is False
    assert resolved.capabilities == {"keyword_search": True}
    assert resolved.degraded_reasons == []
    assert len(uows) == 1
    assert uows[0].entered == uows[0].exited == 1
    assert knowledge_repo.calls[0][0] == "kb-1"
    assert version_repo.calls == [("get", "v2", "kb-1")]


@pytest.mark.asyncio
async def test_explicit_historical_degraded_version_projects_exactly():
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v2"
    )
    degraded = _version(
        "v1",
        state=KnowledgeVersionState.DEGRADED,
        capabilities={"keyword_search": True, "vector_search": False},
        reasons=["EMBEDDING_UNAVAILABLE", "GRAPH_UNAVAILABLE"],
    )
    service, *_ = _service(
        kb=kb,
        versions=[degraded, _version("v2", offset=1)],
    )

    result = await service.resolve_published_version(
        "kb-1",
        "v1",
        OwnerScope.personal("user-1"),
    )

    assert result.state is BuildState.DEGRADED
    assert result.degraded is True
    assert result.capabilities == {
        "keyword_search": True,
        "vector_search": False,
    }
    assert result.degraded_reasons == [
        "EMBEDDING_UNAVAILABLE",
        "GRAPH_UNAVAILABLE",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "reasons"),
    (
        (KnowledgeVersionState.READY, ["CONTRADICTORY"]),
        (KnowledgeVersionState.DEGRADED, []),
    ),
)
async def test_fix1_red_p1_3_provider_rejects_inconsistent_persisted_rows(
    state,
    reasons,
):
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="inconsistent"
    )
    service, *_ = _service(
        kb=kb,
        versions=[
            _version(
                "inconsistent",
                state=state,
                reasons=reasons,
            )
        ],
    )

    with pytest.raises(BadRequestError, match="inconsistent"):
        await service.resolve_published_version(
            "kb-1",
            None,
            OwnerScope.personal("user-1"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    (
        OwnerScope.personal("foreign-user"),
        OwnerScope.team("foreign-user", "foreign-team"),
    ),
)
async def test_foreign_owner_is_non_enumerating_and_does_not_read_versions(scope):
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v2"
    )
    service, _, version_repo, _ = _service(
        kb=kb,
        versions=[_version("v2")],
    )

    with pytest.raises(NotFoundError, match="owner scope"):
        await service.resolve_published_version("kb-1", "v2", scope)
    assert version_repo.calls == []


@pytest.mark.asyncio
async def test_foreign_kb_version_is_not_found_without_state_enumeration():
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v2"
    )
    service, *_ = _service(
        kb=kb,
        versions=[
            _version("v2"),
            _version(
                "foreign-building",
                kb_id="kb-2",
                state=KnowledgeVersionState.BUILDING,
                published=False,
            ),
        ],
    )

    with pytest.raises(NotFoundError, match="version"):
        await service.resolve_published_version(
            "kb-1",
            "foreign-building",
            OwnerScope.personal("user-1"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "published"),
    (
        (KnowledgeVersionState.BUILDING, False),
        (KnowledgeVersionState.FAILED, False),
        (KnowledgeVersionState.READY, False),
        (KnowledgeVersionState.DEGRADED, False),
    ),
)
async def test_owned_unpublished_or_nonterminal_version_is_stable_not_ready(
    state, published
):
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v2"
    )
    service, *_ = _service(
        kb=kb,
        versions=[_version("v2", state=state, published=published)],
    )

    with pytest.raises(BadRequestError, match="not published"):
        await service.resolve_published_version(
            "kb-1", "v2", OwnerScope.personal("user-1")
        )


@pytest.mark.asyncio
async def test_no_active_version_is_stable_not_ready_without_version_read():
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1")
    service, _, version_repo, _ = _service(kb=kb, versions=[])

    with pytest.raises(BadRequestError, match="no published"):
        await service.resolve_published_version(
            "kb-1", None, OwnerScope.personal("user-1")
        )
    assert version_repo.calls == []


@pytest.mark.asyncio
async def test_list_is_scoped_published_only_deterministic_and_includes_active():
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="active"
    )
    versions = [
        _version("old", offset=0),
        _version("active", offset=1),
        _version(
            "building",
            state=KnowledgeVersionState.BUILDING,
            published=False,
            offset=2,
        ),
        _version(
            "failed",
            state=KnowledgeVersionState.FAILED,
            published=False,
            offset=3,
        ),
    ]
    service, *_ = _service(kb=kb, versions=versions)

    listed = await service.list_published_versions(
        "kb-1", OwnerScope.personal("user-1")
    )

    assert [item.version_id for item in listed] == ["active", "old"]
    assert listed[0].version_id == kb.active_version_id


@pytest.mark.asyncio
async def test_list_paginates_without_losing_history():
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v-500"
    )
    versions = [
        _version(f"v-{index:03d}", offset=index)
        for index in range(501)
    ]
    service, _, version_repo, _ = _service(kb=kb, versions=versions)

    listed = await service.list_published_versions(
        "kb-1", OwnerScope.personal("user-1")
    )

    assert len(listed) == 501
    assert listed[0].version_id == "v-500"
    assert listed[-1].version_id == "v-000"
    assert version_repo.calls == [
        ("list", "kb-1", 500, None),
        (
            "list",
            "kb-1",
            500,
            (versions[1].created_at, versions[1].id),
        ),
    ]


class _MutatingKeysetVersionRepo(_VersionRepo):
    async def list_versions(
        self,
        knowledge_base_id: str,
        *,
        limit: int = 500,
        before: tuple[datetime, str] | None = None,
    ):
        self.calls.append(("keyset", knowledge_base_id, limit, before))
        values = [
            item
            for item in self.versions.values()
            if item.knowledge_base_id == knowledge_base_id
            and (
                before is None
                or (item.created_at, item.id) < before
            )
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        page = values[:limit]
        if before is None:
            self.versions.pop("v-499")
            inserted = _version("inserted-newer", offset=1000)
            self.versions[inserted.id] = inserted
        return page


@pytest.mark.asyncio
async def test_fix1_red_p2_1_keyset_survives_interpage_history_mutation():
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v-500"
    )
    versions = [
        _version(f"v-{index:03d}", offset=index)
        for index in range(501)
    ]
    repository = _MutatingKeysetVersionRepo(versions)
    knowledge_repository = _KnowledgeRepo({"kb-1": kb})
    service = KnowledgeVersionService(
        uow_factory=lambda: _Uow(
            knowledge_repository,
            repository,
        )
    )

    listed = await service.list_published_versions(
        "kb-1", OwnerScope.personal("user-1")
    )

    assert {item.version_id for item in listed} == {
        f"v-{index:03d}" for index in range(501)
    }
    assert "inserted-newer" not in {
        item.version_id for item in listed
    }
    assert repository.calls[1][3] == (
        versions[1].created_at,
        versions[1].id,
    )


@pytest.mark.asyncio
async def test_uow_commit_failure_is_propagated_and_not_retried():
    kb = KnowledgeBase(
        id="kb-1", owner_user_id="user-1", active_version_id="v1"
    )
    service, _, version_repo, uows = _service(
        kb=kb,
        versions=[_version("v1")],
        exit_error=RuntimeError("commit failed"),
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        await service.resolve_published_version(
            "kb-1", None, OwnerScope.personal("user-1")
        )
    assert len(uows) == 1
    assert version_repo.calls == [("get", "v1", "kb-1")]


@pytest.mark.asyncio
async def test_db_uow_and_container_wire_the_version_repository_and_provider():
    session = AsyncMock()
    uow = DBUnitOfWork(
        lambda: session,
        authorization_context=AuthorizationContext.system(
            "knowledge-version-uow-test"
        ),
    )

    entered = await uow.__aenter__()
    try:
        assert isinstance(
            entered.knowledge_version, DBKnowledgeVersionRepository
        )
        assert (
            BaseContainer.knowledge_base_version_provider.provides
            is KnowledgeVersionService
        )
    finally:
        await uow.__aexit__(ValueError, ValueError("rollback"), None)
