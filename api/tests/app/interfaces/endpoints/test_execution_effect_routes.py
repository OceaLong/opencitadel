from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.domain.errors import AppException, ConflictError, NotFoundError
from app.domain.models.scope import OwnerScope, Principal, WorkspaceContext
from app.domain.models.user import GlobalRole
from app.interfaces.auth_dependencies import get_current_principal, get_workspace_context
from app.interfaces.endpoints.execution_effect_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_effect_service


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_exception_handlers(app)
    principal = Principal(user_id="owner")
    app.dependency_overrides[get_current_principal] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=OwnerScope.personal("owner")
    )
    service = AsyncMock()
    app.dependency_overrides[get_effect_service] = lambda: service
    run_id, operation_id, request_id = uuid4(), uuid4(), uuid4()
    request = {
        "request_id": request_id,
        "run_id": run_id,
        "operation_id": operation_id,
        "expected_revision": 3,
        "status": "pending",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    for method in ("submit_query", "submit_resolution", "submit_abandon", "get_request"):
        getattr(service, method).return_value = request
    service.list_operations.return_value = []
    with TestClient(app) as http:
        yield (
            http,
            service,
            principal,
            f"/api/execution-runs/{run_id}/effects/{operation_id}",
            request,
        )


def resolution():
    return {
        "expected_revision": 3,
        "decision": "applied",
        "reason": "Reviewed audit record",
        "evidence_refs": [
            {"ref": "file://00000000-0000-0000-0000-000000000001", "sha256": "a" * 64}
        ],
        "result_evidence_ref": {
            "ref": "file://00000000-0000-0000-0000-000000000002",
            "sha256": "b" * 64,
        },
        "attestation": {
            "source": "Audit record 471",
            "action": "scale_workload",
            "target": "c" * 64,
            "occurred_at": "2026-10-13T00:00:00Z",
            "method": "Checked original operation log",
            "responsible_actor": "owner",
        },
    }


@pytest.mark.parametrize(
    ("action", "body", "method"),
    [
        ("queries", {"expected_revision": 3}, "submit_query"),
        ("resolutions", resolution(), "submit_resolution"),
        ("abandon", {"expected_revision": 3, "reason": "Stop waiting"}, "submit_abandon"),
    ],
)
def test_mutations_accept_durable_requests_and_forward_exact_subject(client, action, body, method):
    http, service, principal, path, request = client
    response = http.post(path + "/" + action, json=body, headers={"Idempotency-Key": "request-1"})
    assert response.status_code == 202
    assert response.json()["data"]["request_id"] == str(request["request_id"])
    args = getattr(service, method).call_args.kwargs
    assert args["principal"] == principal
    assert args["scope"] == OwnerScope.personal("owner")
    assert args["run_id"] == request["run_id"]
    assert args["operation_id"] == request["operation_id"]
    assert args["idempotency_key"] == "request-1"
    assert args["expected_revision"] == 3
    assert response.headers["cache-control"] == "no-store"
    assert not any("hold" in name for name in args)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "succeeded"},
        {"release_hold": True},
        {"evidence_refs": []},
        {"decision": "unknown"},
        {"expected_revision": -1},
    ],
)
def test_resolution_cannot_force_status_or_skip_evidence(client, change):
    http, service, _, path, _ = client
    response = http.post(
        path + "/resolutions", json=resolution() | change, headers={"Idempotency-Key": "request-1"}
    )
    assert response.status_code == 422
    service.submit_resolution.assert_not_called()


@pytest.mark.parametrize("key", [None, "", "   "])
def test_request_requires_idempotency_key(client, key):
    http, service, _, path, _ = client
    response = http.post(
        path + "/queries",
        json={"expected_revision": 3},
        headers={} if key is None else {"Idempotency-Key": key},
    )
    assert response.status_code == 400
    service.submit_query.assert_not_called()


@pytest.mark.parametrize("action", ["queries", "resolutions", "abandon"])
def test_auditor_is_read_only_even_without_outer_router(client, action):
    http, service, principal, path, _ = client
    principal.global_role = GlobalRole.AUDITOR
    response = http.post(
        path + "/" + action, json=resolution(), headers={"Idempotency-Key": "request-1"}
    )
    assert response.status_code == 403
    assert not service.submit_query.called
    assert not service.submit_resolution.called
    assert not service.submit_abandon.called


def test_cross_scope_requires_admin_and_explicit_reason(client):
    http, service, principal, path, _ = client
    headers = {"X-Effect-Scope-User-Id": "another", "X-Effect-Scope-Reason": "Incident 17"}
    assert http.get(path.rsplit("/", 1)[0], headers=headers).status_code == 403
    service.list_operations.assert_not_called()
    principal.global_role = GlobalRole.ADMIN
    assert (
        http.get(path.rsplit("/", 1)[0], headers={"X-Effect-Scope-User-Id": "another"}).status_code
        == 400
    )
    response = http.get(path.rsplit("/", 1)[0], headers=headers)
    assert response.status_code == 200
    assert service.list_operations.call_args.kwargs["scope"] == OwnerScope.personal("another")
    assert service.list_operations.call_args.kwargs["scope_reason"] == "Incident 17"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ConflictError(), 409),
        (NotFoundError(), 404),
        (AppException(status_code=422, code=422, msg="Invalid evidence"), 422),
    ],
)
def test_authority_failures_preserve_http_semantics(client, error, status):
    http, service, _, path, _ = client
    service.submit_resolution.side_effect = error
    result = http.post(path + "/resolutions", json=resolution(), headers={"Idempotency-Key": "key"})
    assert result.status_code == status


def test_request_polling_binds_request_to_run_operation_and_scope(client):
    http, service, principal, path, request = client
    response = http.get(path + "/requests/" + str(request["request_id"]))
    assert response.status_code == 200
    assert service.get_request.call_args.kwargs == {
        "run_id": request["run_id"],
        "operation_id": request["operation_id"],
        "request_id": request["request_id"],
        "scope": OwnerScope.personal("owner"),
        "principal": principal,
        "scope_reason": None,
    }


def test_effect_reads_accept_the_real_service_summary_shape(client):
    from app.application.execution.effect_service import EffectService
    from app.domain.execution.effect import EffectOperationSpec, EffectState

    http, service, _, path, request = client
    spec = EffectOperationSpec(
        operation_id=request["operation_id"],
        run_id=request["run_id"],
        invocation_id=uuid4(),
        activity_id=uuid4(),
        generation=0,
        provider_identity="ops-actuator",
        provider_locator={"action": "scale_workload"},
        contract_digest="a" * 64,
        arguments_digest="b" * 64,
        target_digest="c" * 64,
        idempotency_key="original",
        capability="dedupe_resolve",
    )
    summary = EffectService._summary(
        {
            "body": EffectState(spec=spec, state="unknown").model_dump(mode="json"),
            "query_history": [request],
        }
    )
    service.get_operation.return_value = summary
    service.list_operations.return_value = [summary]
    for target in (path, path.rsplit("/", 1)[0]):
        response = http.get(target)
        assert response.status_code == 200
    assert http.get(path).json()["data"]["verification_binding"]["action"] == "scale_workload"
