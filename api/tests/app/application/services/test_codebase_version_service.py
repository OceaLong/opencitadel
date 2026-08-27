"""Codebase-specific provider contract, plus cross-provider mirror cases.

``resolve_published_version``/``list_published_versions`` behave identically
for ``KnowledgeVersionService`` and ``CodebaseVersionService`` (both extend
``OwnerScopedVersionProvider``). The cases that mirror 1:1 across the two
providers are parametrized here so both provider lines run through one
assertion body while still asserting each provider's own error text. The
knowledge-base-only enrichments (team scope, one-UoW call-log invariant,
keyset pagination, UoW commit-failure propagation, DB wiring) stay in
``test_knowledge_version_service.py``. The inconsistent-degradation-metadata
case (added in Task 13) stays duplicated per file on purpose, per the task
brief, since it is a domain-specific regression guard rather than a mirror.
"""

import re
from collections.abc import Callable
from typing import Any

import pytest

from app.application.services.codebase_version_service import CodebaseVersionService
from app.application.services.knowledge_version_service import KnowledgeVersionService
from app.domain.errors import BadRequestError, NotFoundError
from app.domain.models.codebase import Codebase
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import KnowledgeVersionState
from app.domain.models.resource_bindings import PublicationState, ResourceKind
from tests.conftest import (
    FakeCodebaseRepo,
    FakeCodebaseVersionRepo,
    FakeKnowledgeBaseRepo,
    FakeKnowledgeVersionRepo,
    FakeUnitOfWork,
    make_codebase_version,
    make_kb_version,
    make_owner_scope,
)


def _kb_case(
    *,
    resource_id: str = "res-1",
    owner_user_id: str = "owner",
    active_version_id: str | None = "v1",
) -> dict[str, Any]:
    resource = KnowledgeBase(
        id=resource_id,
        owner_user_id=owner_user_id,
        active_version_id=active_version_id,
    )

    def version(version_id: str, **overrides: Any):
        overrides.setdefault("knowledge_base_id", resource_id)
        return make_kb_version(version_id, **overrides)

    def uow(versions: list):
        return FakeUnitOfWork(
            knowledge_base=FakeKnowledgeBaseRepo({resource_id: resource}),
            knowledge_version=FakeKnowledgeVersionRepo(versions),
        )

    return {
        "resource": resource,
        "version": version,
        "uow": uow,
        "resource_id_field": "knowledge_base_id",
        "READY": KnowledgeVersionState.READY,
        "DEGRADED": KnowledgeVersionState.DEGRADED,
        "BUILDING": KnowledgeVersionState.BUILDING,
        "FAILED": KnowledgeVersionState.FAILED,
    }


def _codebase_case(
    *,
    resource_id: str = "res-1",
    owner_user_id: str = "owner",
    active_version_id: str | None = "v1",
) -> dict[str, Any]:
    resource = Codebase(
        id=resource_id,
        owner_user_id=owner_user_id,
        active_version_id=active_version_id,
    )

    def version(version_id: str, **overrides: Any):
        overrides.setdefault("codebase_id", resource_id)
        return make_codebase_version(version_id, **overrides)

    def uow(versions: list):
        return FakeUnitOfWork(
            codebase=FakeCodebaseRepo({resource_id: resource}),
            codebase_version=FakeCodebaseVersionRepo(versions),
        )

    return {
        "resource": resource,
        "version": version,
        "uow": uow,
        "resource_id_field": "codebase_id",
        "READY": CodebaseVersionState.READY,
        "DEGRADED": CodebaseVersionState.DEGRADED,
        "BUILDING": CodebaseVersionState.BUILDING,
        "FAILED": CodebaseVersionState.FAILED,
    }


_PROVIDERS = (
    pytest.param(
        KnowledgeVersionService,
        ResourceKind.KNOWLEDGE_BASE,
        _kb_case,
        "knowledge-base version",
        id="knowledge_base",
    ),
    pytest.param(
        CodebaseVersionService,
        ResourceKind.CODEBASE,
        _codebase_case,
        "codebase version",
        id="codebase",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_cls", "resource_kind", "case_builder", "version_label"), _PROVIDERS
)
async def test_resolve_published_version_active_and_explicit_degraded(
    service_cls: type,
    resource_kind: ResourceKind,
    case_builder: Callable[..., dict[str, Any]],
    version_label: str,
):
    case = case_builder(active_version_id="ready-v1")
    ready = case["version"]("ready-v1")
    degraded = case["version"](
        "degraded-v2",
        state=case["DEGRADED"],
        capabilities={"lexical_search": True, "vector_search": False},
        degraded_reasons=["EMBEDDING_UNAVAILABLE"],
    )
    service = service_cls(uow_factory=lambda: case["uow"]([ready, degraded]))
    scope = make_owner_scope(user_id="owner")

    active = await service.resolve_published_version(case["resource"].id, None, scope)
    explicit_degraded = await service.resolve_published_version(
        case["resource"].id, "degraded-v2", scope
    )

    assert active.resource_kind is resource_kind
    assert active.version_id == "ready-v1"
    assert active.state is PublicationState.READY
    assert explicit_degraded.version_id == "degraded-v2"
    assert explicit_degraded.state is PublicationState.DEGRADED
    assert explicit_degraded.degraded_reasons == ["EMBEDDING_UNAVAILABLE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state_name", ["BUILDING", "FAILED", "READY", "DEGRADED"])
@pytest.mark.parametrize(
    ("service_cls", "resource_kind", "case_builder", "version_label"), _PROVIDERS
)
async def test_resolve_published_version_rejects_unpublished_or_nonterminal(
    service_cls: type,
    resource_kind: ResourceKind,
    case_builder: Callable[..., dict[str, Any]],
    version_label: str,
    state_name: str,
):
    case = case_builder(active_version_id="v1")
    version = case["version"]("v1", state=case[state_name], published=False)
    service = service_cls(uow_factory=lambda: case["uow"]([version]))

    with pytest.raises(BadRequestError, match=re.escape(f"{version_label} is not published")):
        await service.resolve_published_version(
            case["resource"].id, "v1", make_owner_scope(user_id="owner")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign_state_name",
    [
        # A ready, published version belonging to a *different* resource must
        # still be a plain "not found" — the id lookup, not the state, is
        # what gates access.
        "READY",
        # Regression guard: a foreign version that is ALSO non-terminal and
        # unpublished must still resolve to NotFoundError (state parity with
        # the owned case), never leak as a BadRequestError — otherwise the
        # error *type* would let a caller distinguish "exists but building"
        # from "doesn't exist in my scope" across resource boundaries (a
        # cross-resource state-enumeration side channel).
        "BUILDING",
    ],
)
@pytest.mark.parametrize(
    ("service_cls", "resource_kind", "case_builder", "version_label"), _PROVIDERS
)
async def test_resolve_published_version_rejects_foreign_version_id(
    service_cls: type,
    resource_kind: ResourceKind,
    case_builder: Callable[..., dict[str, Any]],
    version_label: str,
    foreign_state_name: str,
):
    case = case_builder(active_version_id="v1")
    own_version = case["version"]("v1")
    foreign_version = case["version"](
        "foreign-v1",
        state=case[foreign_state_name],
        published=foreign_state_name == "READY",
        **{case["resource_id_field"]: "other-resource"},
    )
    service = service_cls(uow_factory=lambda: case["uow"]([own_version, foreign_version]))

    with pytest.raises(NotFoundError, match=re.escape(f"{version_label} not found in owner scope")):
        await service.resolve_published_version(
            case["resource"].id, "foreign-v1", make_owner_scope(user_id="owner")
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_cls", "resource_kind", "case_builder", "version_label"), _PROVIDERS
)
async def test_list_published_versions_filters_and_orders_desc(
    service_cls: type,
    resource_kind: ResourceKind,
    case_builder: Callable[..., dict[str, Any]],
    version_label: str,
):
    case = case_builder(active_version_id="active")
    versions = [
        case["version"]("old", offset=0),
        case["version"]("active", offset=1),
        case["version"]("building", state=case["BUILDING"], published=False, offset=2),
        case["version"]("failed", state=case["FAILED"], published=False, offset=3),
    ]
    service = service_cls(uow_factory=lambda: case["uow"](versions))

    listed = await service.list_published_versions(
        case["resource"].id, make_owner_scope(user_id="owner")
    )

    assert [item.version_id for item in listed] == ["active", "old"]
    assert listed[0].version_id == case["resource"].active_version_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "reasons"),
    [
        (CodebaseVersionState.READY, ["CONTRADICTORY"]),
        (CodebaseVersionState.DEGRADED, []),
    ],
)
async def test_fix1_red_p1_3_provider_rejects_inconsistent_persisted_rows(
    state: CodebaseVersionState,
    reasons: list[str],
):
    codebase = Codebase(id="cb1", owner_user_id="owner", active_version_id="inconsistent")
    version = make_codebase_version(
        "inconsistent",
        codebase_id="cb1",
        state=state,
        degraded_reasons=reasons,
    )
    uow = FakeUnitOfWork(
        codebase=FakeCodebaseRepo({"cb1": codebase}),
        codebase_version=FakeCodebaseVersionRepo([version]),
    )
    service = CodebaseVersionService(uow_factory=lambda: uow)

    with pytest.raises(BadRequestError, match="inconsistent"):
        await service.resolve_published_version("cb1", None, make_owner_scope(user_id="owner"))
