from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.errors import ForbiddenError
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.domain.runtime_policy import (
    ActiveExecutionPolicy,
    AgentExecutionPolicy,
    ExecutionPolicy,
    ExecutionPolicyRevision,
    OperationsPolicy,
    RuntimePolicyHead,
    RuntimePolicyHeadConflictError,
    policy_digest,
)
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.endpoints.runtime_policy_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_runtime_policy_service

NOW = datetime(2026, 8, 26, 4, 0, tzinfo=UTC)


def _admin_context() -> WorkspaceContext:
    principal = Principal(user_id="admin-1", global_role=GlobalRole.ADMIN)
    return WorkspaceContext(
        principal=principal,
        scope=OwnerScope.personal(principal.user_id),
    )


def _active_execution() -> ActiveExecutionPolicy:
    execution_id = uuid4()
    operations_id = uuid4()
    policy = ExecutionPolicy(agent=AgentExecutionPolicy(max_iterations=17))
    head = RuntimePolicyHead(
        version=4,
        execution_revision_id=execution_id,
        operations_revision_id=operations_id,
        updated_by="admin-1",
        updated_at=NOW,
    )
    return ActiveExecutionPolicy(
        head=head,
        revision=ExecutionPolicyRevision(
            id=execution_id,
            sequence=4,
            schema_version=1,
            policy=policy,
            digest=policy_digest(1, policy),
            created_by="admin-1",
            note="active execution",
            created_at=NOW,
        ),
    )


def _app(*, service: AsyncMock, deny: bool = False) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    context = _admin_context()

    async def _require_admin():
        if deny:
            raise ForbiddenError("需要管理员权限", error_key="errors.adminRequired")
        return context.principal

    async def _workspace_context() -> WorkspaceContext:
        return context

    async def _service() -> AsyncMock:
        return service

    app.dependency_overrides[require_admin] = _require_admin
    app.dependency_overrides[get_workspace_context] = _workspace_context
    app.dependency_overrides[get_runtime_policy_service] = _service
    return app


def _requests() -> list[tuple[str, str, dict | None]]:
    active = _active_execution()
    create_common = {
        "expected_head_version": active.head.version,
        "expected_active_revision_id": str(active.revision.id),
        "note": "bounded administrative change",
    }
    restore = dict(create_common)
    return [
        ("GET", "/api/runtime-policies/execution", None),
        ("GET", "/api/runtime-policies/execution/revisions", None),
        (
            "POST",
            "/api/runtime-policies/execution/revisions",
            {**create_common, "policy": ExecutionPolicy().model_dump(mode="json")},
        ),
        (
            "POST",
            f"/api/runtime-policies/execution/revisions/{uuid4()}/restore",
            restore,
        ),
        ("GET", "/api/runtime-policies/operations", None),
        ("GET", "/api/runtime-policies/operations/revisions", None),
        (
            "POST",
            "/api/runtime-policies/operations/revisions",
            {**create_common, "policy": OperationsPolicy().model_dump(mode="json")},
        ),
        (
            "POST",
            f"/api/runtime-policies/operations/revisions/{uuid4()}/restore",
            restore,
        ),
    ]


@pytest.mark.parametrize(("method", "path", "body"), _requests())
def test_every_runtime_policy_route_is_admin_only(
    method: str,
    path: str,
    body: dict | None,
) -> None:
    service = AsyncMock()

    with TestClient(_app(service=service, deny=True)) as client:
        response = client.request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["error_key"] == "errors.adminRequired"
    assert not service.mock_calls


def test_stale_head_returns_current_head_metadata() -> None:
    service = AsyncMock()
    active = _active_execution()
    service.create_execution.side_effect = RuntimePolicyHeadConflictError(active.head)
    body = {
        "expected_head_version": active.head.version,
        "expected_active_revision_id": str(active.revision.id),
        "policy": active.revision.policy.model_dump(mode="json"),
        "note": "conflicting edit",
    }

    with TestClient(_app(service=service)) as client:
        response = client.post("/api/runtime-policies/execution/revisions", json=body)

    assert response.status_code == 409
    payload = response.json()
    assert payload["error_key"] == "runtimePolicy.headConflict"
    assert payload["data"]["version"] == active.head.version
    assert payload["data"]["execution_revision_id"] == str(active.revision.id)


def test_actor_is_derived_and_unknown_request_fields_are_rejected() -> None:
    service = AsyncMock()
    active = _active_execution()
    service.create_execution.return_value = active
    body = {
        "expected_head_version": active.head.version,
        "expected_active_revision_id": str(active.revision.id),
        "policy": active.revision.policy.model_dump(mode="json"),
        "note": "typed edit",
    }

    with TestClient(_app(service=service)) as client:
        accepted = client.post("/api/runtime-policies/execution/revisions", json=body)
        rejected = client.post(
            "/api/runtime-policies/execution/revisions",
            json={**body, "created_by": "forged-admin"},
        )

    assert accepted.status_code == 200
    service.create_execution.assert_awaited_once_with(
        policy=active.revision.policy,
        expected_head_version=active.head.version,
        expected_active_revision_id=active.revision.id,
        note="typed edit",
        actor_user_id="admin-1",
    )
    assert rejected.status_code == 422
