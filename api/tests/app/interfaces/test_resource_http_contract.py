#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Real FastAPI HTTP contracts for governed resource/session boundaries."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.domain.errors import NotFoundError
from app.application.security.authorization_context import (
    reset_authorization_context,
    set_authorization_context,
)
from app.application.services.codebase_service import CodebaseService
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.application.services.resource_guard_service import ResourceGuardService
from app.application.services.session_service import SessionService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.codebase import (
    Codebase,
    CodebaseSourceType,
    CodebaseStatus,
    SessionMode,
)
from app.domain.models.knowledge_base import KBStatus, KnowledgeBase
from app.domain.models.resource_governance import (
    BuildState,
    PublishedResourceVersion,
    ResourceKind,
    SessionResourceBinding,
)
from app.domain.models.scope import Principal
from app.domain.models.user import GlobalRole
from app.domain.services.resource_version_provider import (
    ResourceVersionProviderRegistry,
)
from app.interfaces.auth_context import set_principal
from app.interfaces.auth_dependencies import enforce_auditor_read_only
from app.interfaces.endpoints import (
    codebase_routes,
    knowledge_base_routes,
    resource_governance_routes,
    session_routes,
)
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import (
    get_audit_service,
    get_codebase_service,
    get_knowledge_base_service,
    get_object_storage,
    get_quota_service,
    get_resource_binding_service,
    get_session_service,
)


class _NeverCalledService:
    def __getattr__(self, name):
        raise AssertionError(f"Auditor request reached service method {name}")


def _http_app(
    *,
    session_service=object(),
    knowledge_base_service=object(),
    codebase_service=object(),
    binding_service=object(),
) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.middleware("http")
    async def inject_test_principal(request, call_next):
        identity = request.headers.get("X-Test-Principal", "owner")
        role = (
            GlobalRole.AUDITOR
            if identity == "auditor"
            else GlobalRole.USER
        )
        principal = Principal(user_id=identity, global_role=role)
        principal_token = set_principal(principal)
        authorization_token = set_authorization_context(
            AuthorizationContext.for_principal(principal)
        )
        try:
            return await call_next(request)
        finally:
            reset_authorization_context(authorization_token)
            principal_token.var.reset(principal_token)

    authenticated = APIRouter(
        dependencies=[Depends(enforce_auditor_read_only)]
    )
    authenticated.include_router(session_routes.router)
    authenticated.include_router(resource_governance_routes.router)
    authenticated.include_router(knowledge_base_routes.router)
    authenticated.include_router(codebase_routes.router)
    app.include_router(authenticated, prefix="/api")

    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_knowledge_base_service] = (
        lambda: knowledge_base_service
    )
    app.dependency_overrides[get_codebase_service] = lambda: codebase_service
    app.dependency_overrides[get_resource_binding_service] = (
        lambda: binding_service
    )
    app.dependency_overrides[get_object_storage] = object
    app.dependency_overrides[get_quota_service] = lambda: SimpleNamespace(
        check_session_quota=_async_noop,
    )
    app.dependency_overrides[get_audit_service] = lambda: SimpleNamespace(
        record=_async_noop,
    )
    return app


async def _async_noop(*_args, **_kwargs):
    return None


_AUDITOR_MUTATIONS = [
    ("POST", "/api/sessions", {}),
    ("POST", "/api/knowledge-bases", {"name": "KB"}),
    ("DELETE", "/api/knowledge-bases/kb1", None),
    (
        "POST",
        "/api/knowledge-bases/kb1/documents",
        {"file_ids": ["f1"]},
    ),
    ("DELETE", "/api/knowledge-bases/kb1/documents/d1", None),
    ("POST", "/api/knowledge-bases/kb1/reindex", None),
    (
        "POST",
        "/api/knowledge-bases/kb1/sessions",
        {"mode": "agent"},
    ),
    (
        "POST",
        "/api/codebases",
        {"name": "Code", "source_type": "files", "file_ids": []},
    ),
    ("POST", "/api/codebases/cb1/source", {"path": "main.py"}),
    ("POST", "/api/codebases/cb1/snapshots", None),
    (
        "POST",
        "/api/codebases/cb1/sessions",
        {"mode": "agent"},
    ),
    ("DELETE", "/api/codebases/cb1", None),
    (
        "POST",
        "/api/sessions/s-owner/resource-bindings/codebase/upgrade",
        {"target_version_id": "cb1-v2"},
    ),
]


@pytest.mark.parametrize(("method", "path", "body"), _AUDITOR_MUTATIONS)
def test_real_http_auditor_is_denied_for_full_resource_mutation_matrix(
    method,
    path,
    body,
):
    never = _NeverCalledService()
    app = _http_app(
        session_service=never,
        knowledge_base_service=never,
        codebase_service=never,
        binding_service=never,
    )

    with TestClient(app) as client:
        response = client.request(
            method,
            path,
            headers={"X-Test-Principal": "auditor"},
            json=body,
        )

    assert response.status_code == 403


class _ReadinessProvider:
    def __init__(self, kind: ResourceKind) -> None:
        self.kind = kind

    async def resolve_published_version(
        self,
        resource_id,
        requested_version_id,
        _scope,
    ):
        ready = not resource_id.startswith("building-")
        return PublishedResourceVersion(
            resource_kind=self.kind,
            resource_id=resource_id,
            version_id=requested_version_id or f"{resource_id}-v1",
            state=BuildState.SUCCEEDED if ready else BuildState.RUNNING,
            published=ready,
        )


class _ReadinessSessionRepository:
    def __init__(self) -> None:
        self.saved = []

    async def save(self, session):
        self.saved.append(session)


class _ReadinessKnowledgeBaseRepository:
    async def get_kb(self, kb_id, scope=None):
        if scope is not None and scope.user_id != "owner":
            return None
        return KnowledgeBase(
            id=kb_id,
            name="KB",
            status=(
                KBStatus.PENDING
                if kb_id.startswith("building-")
                else KBStatus.READY
            ),
            ready_doc_count=1,
            owner_user_id="owner",
        )

    async def count_ready_documents(self, kb_ids):
        return {kb_id: 1 for kb_id in kb_ids}


class _ReadinessCodebaseRepository:
    async def get_by_id(self, codebase_id, scope=None):
        if scope is not None and scope.user_id != "owner":
            return None
        return Codebase(
            id=codebase_id,
            name="Code",
            source_type=CodebaseSourceType.FILES,
            status=(
                CodebaseStatus.PENDING
                if codebase_id.startswith("building-")
                else CodebaseStatus.READY
            ),
            owner_user_id="owner",
        )


class _MissingOptionalRepository:
    async def get_by_id(self, _item_id, scope=None):
        del scope
        return None


class _ReadinessUow:
    def __init__(self, session_repository) -> None:
        self.session = session_repository
        self.knowledge_base = _ReadinessKnowledgeBaseRepository()
        self.codebase = _ReadinessCodebaseRepository()
        self.llm_model = _MissingOptionalRepository()
        self.skill = _MissingOptionalRepository()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _readiness_services():
    session_repository = _ReadinessSessionRepository()
    uow_factory = lambda: _ReadinessUow(session_repository)
    providers = ResourceVersionProviderRegistry([
        _ReadinessProvider(ResourceKind.KNOWLEDGE_BASE),
        _ReadinessProvider(ResourceKind.CODEBASE),
    ])
    guard = ResourceGuardService(providers=providers)
    return (
        SessionService(
            uow_factory=uow_factory,
            sandbox_cls=MagicMock(),
            resource_guard=guard,
        ),
        KnowledgeBaseService(
            uow_factory=uow_factory,
            file_storage=MagicMock(),
            resource_guard=guard,
        ),
        CodebaseService(
            uow_factory=uow_factory,
            sandbox_cls=MagicMock(),
            file_storage=MagicMock(),
            resource_guard=guard,
        ),
    )


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/sessions",
            {"knowledge_base_id": "building-kb"},
        ),
        (
            "/api/knowledge-bases/building-kb/sessions",
            {"mode": "ask"},
        ),
        (
            "/api/codebases/building-cb/sessions",
            {"mode": "ask"},
        ),
    ],
)
def test_real_http_generic_kb_and_codebase_creation_share_readiness_rejection(
    path,
    body,
):
    generic, knowledge, codebase = _readiness_services()
    app = _http_app(
        session_service=generic,
        knowledge_base_service=knowledge,
        codebase_service=codebase,
    )

    with TestClient(app) as client:
        response = client.post(
            path,
            headers={"X-Test-Principal": "owner"},
            json=body,
        )

    assert response.status_code == 400
    assert response.json()["msg"] == "resource version is not published"


def test_real_http_kb_session_preserves_explicit_agent_mode():
    generic, knowledge, codebase = _readiness_services()
    app = _http_app(
        session_service=generic,
        knowledge_base_service=knowledge,
        codebase_service=codebase,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/knowledge-bases/ready-kb/sessions",
            headers={"X-Test-Principal": "owner"},
            json={"mode": "agent"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == SessionMode.AGENT.value


class _OwnerBindingService:
    def __init__(self) -> None:
        self.current_binding = SessionResourceBinding(
            id="b1",
            session_id="s-owner",
            resource_kind=ResourceKind.CODEBASE,
            resource_id="cb1",
            version_id="cb1-v1",
            bound_by="owner",
        )

    @staticmethod
    def _assert_owner(session_id, scope):
        if session_id != "s-owner" or scope.user_id != "owner":
            raise NotFoundError("session not found in owner scope")

    async def current_bindings(self, session_id, scope):
        self._assert_owner(session_id, scope)
        return [self.current_binding]

    async def current(self, session_id, kind, scope):
        self._assert_owner(session_id, scope)
        assert kind is ResourceKind.CODEBASE
        return self.current_binding

    async def upgrade(
        self,
        session_id,
        kind,
        target_version_id,
        *,
        actor_id,
        scope,
    ):
        self._assert_owner(session_id, scope)
        assert actor_id == "owner"
        self.current_binding = self.current_binding.model_copy(update={
            "id": "b2",
            "version_id": target_version_id,
            "supersedes_binding_id": "b1",
        })
        return self.current_binding


def test_real_http_binding_list_and_upgrade_enforce_owner_isolation():
    binding = _OwnerBindingService()
    app = _http_app(binding_service=binding)

    with TestClient(app) as client:
        owner_list = client.get(
            "/api/sessions/s-owner/resource-bindings",
            headers={"X-Test-Principal": "owner"},
        )
        foreign_list = client.get(
            "/api/sessions/s-owner/resource-bindings",
            headers={"X-Test-Principal": "other"},
        )
        foreign_upgrade = client.post(
            "/api/sessions/s-owner/resource-bindings/codebase/upgrade",
            headers={"X-Test-Principal": "other"},
            json={"target_version_id": "cb1-v2"},
        )
        owner_upgrade = client.post(
            "/api/sessions/s-owner/resource-bindings/codebase/upgrade",
            headers={"X-Test-Principal": "owner"},
            json={"target_version_id": "cb1-v2"},
        )

    assert owner_list.status_code == 200
    assert foreign_list.status_code == 404
    assert foreign_upgrade.status_code == 404
    assert owner_upgrade.status_code == 200
    assert owner_upgrade.json()["data"] == {
        "old_binding_id": "b1",
        "new_binding_id": "b2",
        "current_version_id": "cb1-v2",
    }


class _SnapshotService:
    def __init__(self) -> None:
        self.package_calls = 0
        self.codebase = SimpleNamespace(
            snapshot_key="existing.tgz",
            updated_at=datetime(2026, 7, 29),
        )

    async def get_codebase(self, codebase_id, *, scope):
        if codebase_id != "cb1" or scope.user_id != "owner":
            raise NotFoundError("codebase not found in owner scope")
        return self.codebase

    async def package_download(self, codebase_id, _storage, *, scope):
        await self.get_codebase(codebase_id, scope=scope)
        self.package_calls += 1
        self.codebase.snapshot_key = "new.tgz"
        return self.codebase.snapshot_key


def test_real_http_snapshot_post_mutates_exactly_once():
    codebase = _SnapshotService()
    app = _http_app(codebase_service=codebase)

    with TestClient(app) as client:
        assert codebase.package_calls == 0
        snapshot = client.post(
            "/api/codebases/cb1/snapshots",
            headers={"X-Test-Principal": "owner"},
        )

    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["snapshot_key"] == "new.tgz"
    assert codebase.package_calls == 1
