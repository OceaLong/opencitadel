"""Owner-scoped provider contract for immutable knowledge versions."""

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.application.services.knowledge_version_service import (
    KnowledgeVersionService,
)
from app.domain.errors import BadRequestError, NotFoundError
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_bindings import PublicationState, ResourceKind
from app.domain.models.scope import OwnerScopeType
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)
from app.infrastructure.repositories.db_uow import DBUnitOfWork
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from tests.conftest import (
    FakeKnowledgeBaseRepo,
    FakeKnowledgeVersionRepo,
    FakeUnitOfWork,
    make_kb_version,
    make_owner_scope,
)


def _service(
    *,
    kb: KnowledgeBase,
    versions: list[KnowledgeBaseVersion],
    exit_error: Exception | None = None,
):
    knowledge_repo = FakeKnowledgeBaseRepo({kb.id: kb})
    version_repo = FakeKnowledgeVersionRepo(versions)
    created: list[FakeUnitOfWork] = []

    def factory():
        uow = FakeUnitOfWork(
            knowledge_base=knowledge_repo,
            knowledge_version=version_repo,
            exit_error=exit_error,
        )
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
    [
        (
            KnowledgeBase(
                id="kb-1",
                owner_user_id="user-1",
                active_version_id="v2",
            ),
            make_owner_scope(user_id="user-1"),
        ),
        (
            KnowledgeBase(
                id="kb-1",
                team_id="team-1",
                active_version_id="v2",
            ),
            make_owner_scope(type=OwnerScopeType.TEAM, user_id="member-1", team_id="team-1"),
        ),
    ],
)
async def test_defaults_to_owner_scoped_active_version_in_one_uow(kb, scope):
    service, knowledge_repo, version_repo, uows = _service(
        kb=kb,
        versions=[make_kb_version("v2")],
    )

    resolved = await service.resolve_published_version("kb-1", None, scope)

    assert resolved.resource_kind is ResourceKind.KNOWLEDGE_BASE
    assert resolved.resource_id == "kb-1"
    assert resolved.version_id == "v2"
    assert resolved.state is PublicationState.READY
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
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1", active_version_id="v2")
    degraded = make_kb_version(
        "v1",
        state=KnowledgeVersionState.DEGRADED,
        capabilities={"keyword_search": True, "vector_search": False},
        degraded_reasons=["EMBEDDING_UNAVAILABLE", "GRAPH_UNAVAILABLE"],
    )
    service, *_ = _service(
        kb=kb,
        versions=[degraded, make_kb_version("v2", offset=1)],
    )

    result = await service.resolve_published_version(
        "kb-1",
        "v1",
        make_owner_scope(user_id="user-1"),
    )

    assert result.state is PublicationState.DEGRADED
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
    [
        (KnowledgeVersionState.READY, ["CONTRADICTORY"]),
        (KnowledgeVersionState.DEGRADED, []),
    ],
)
async def test_fix1_red_p1_3_provider_rejects_inconsistent_persisted_rows(
    state,
    reasons,
):
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1", active_version_id="inconsistent")
    service, *_ = _service(
        kb=kb,
        versions=[
            make_kb_version(
                "inconsistent",
                state=state,
                degraded_reasons=reasons,
            )
        ],
    )

    with pytest.raises(BadRequestError, match="inconsistent"):
        await service.resolve_published_version(
            "kb-1",
            None,
            make_owner_scope(user_id="user-1"),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        make_owner_scope(user_id="foreign-user"),
        make_owner_scope(
            type=OwnerScopeType.TEAM,
            user_id="foreign-user",
            team_id="foreign-team",
        ),
    ],
)
async def test_foreign_owner_is_non_enumerating_and_does_not_read_versions(scope):
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1", active_version_id="v2")
    service, _, version_repo, _ = _service(
        kb=kb,
        versions=[make_kb_version("v2")],
    )

    with pytest.raises(NotFoundError, match="owner scope"):
        await service.resolve_published_version("kb-1", "v2", scope)
    assert version_repo.calls == []


@pytest.mark.asyncio
async def test_no_active_version_is_stable_not_ready_without_version_read():
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1")
    service, _, version_repo, _ = _service(kb=kb, versions=[])

    with pytest.raises(BadRequestError, match="no published"):
        await service.resolve_published_version("kb-1", None, make_owner_scope(user_id="user-1"))
    assert version_repo.calls == []


@pytest.mark.asyncio
async def test_list_paginates_without_losing_history():
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1", active_version_id="v-500")
    versions = [make_kb_version(f"v-{index:03d}", offset=index) for index in range(501)]
    service, _, version_repo, _ = _service(kb=kb, versions=versions)

    listed = await service.list_published_versions("kb-1", make_owner_scope(user_id="user-1"))

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


class _MutatingKeysetVersionRepo(FakeKnowledgeVersionRepo):
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
            and (before is None or (item.created_at, item.id) < before)
        ]
        values.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        page = values[:limit]
        if before is None:
            self.versions.pop("v-499")
            inserted = make_kb_version("inserted-newer", offset=1000)
            self.versions[inserted.id] = inserted
        return page


@pytest.mark.asyncio
async def test_fix1_red_p2_1_keyset_survives_interpage_history_mutation():
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1", active_version_id="v-500")
    versions = [make_kb_version(f"v-{index:03d}", offset=index) for index in range(501)]
    repository = _MutatingKeysetVersionRepo(versions)
    knowledge_repository = FakeKnowledgeBaseRepo({"kb-1": kb})
    service = KnowledgeVersionService(
        uow_factory=lambda: FakeUnitOfWork(
            knowledge_base=knowledge_repository,
            knowledge_version=repository,
        )
    )

    listed = await service.list_published_versions("kb-1", make_owner_scope(user_id="user-1"))

    assert {item.version_id for item in listed} == {f"v-{index:03d}" for index in range(501)}
    assert "inserted-newer" not in {item.version_id for item in listed}
    assert repository.calls[1][3] == (
        versions[1].created_at,
        versions[1].id,
    )


@pytest.mark.asyncio
async def test_read_only_resolution_does_not_commit():
    kb = KnowledgeBase(id="kb-1", owner_user_id="user-1", active_version_id="v1")
    service, _, version_repo, uows = _service(
        kb=kb,
        versions=[make_kb_version("v1")],
        exit_error=RuntimeError("commit failed"),
    )

    resolved = await service.resolve_published_version(
        "kb-1",
        None,
        make_owner_scope(user_id="user-1"),
    )
    assert resolved.version_id == "v1"
    assert len(uows) == 1
    assert uows[0].commits == 0
    assert uows[0].rollbacks == 1
    assert version_repo.calls == [("get", "v1", "kb-1")]


@pytest.mark.asyncio
async def test_db_uow_wires_the_version_repository():
    session = AsyncMock()
    uow = DBUnitOfWork(
        lambda: session,
        secret_cipher=ApiKeyCipher("knowledge-version-uow-test-secret"),
        audit_signing_key="knowledge-version-audit-signing-key",
        audit_signing_key_id="test",
        database_authorization_signing_secret="knowledge-version-authorization-secret",
        authorization_context=AuthorizationContext.system("knowledge-version-uow-test"),
    )

    entered = await uow.__aenter__()
    try:
        assert isinstance(entered.knowledge_version, DBKnowledgeVersionRepository)
    finally:
        await uow.__aexit__(ValueError, ValueError("rollback"), None)
