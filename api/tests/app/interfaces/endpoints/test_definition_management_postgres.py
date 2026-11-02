# ruff: noqa: F401,F811 -- real, separate API and kernel database roles
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.application.execution.definition_management import DefinitionManagementService
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import WorkspaceContext
from app.infrastructure.execution.definition_management import PostgresDefinitionManagement
from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from app.interfaces.auth_dependencies import get_workspace_context, require_admin
from app.interfaces.endpoints.execution_definition_routes import router
from app.interfaces.errors.exception_handlers import register_exception_handlers
from app.interfaces.service_dependencies import get_definition_management_service
from tests.app.infrastructure.execution.test_definition_lifecycle import (
    capability,
    datasets,
    definition_runtime,
    fresh_f07_database,
    isolated_database,
    manifest,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_admin_head_and_retire_use_durable_kernel_authority_and_idempotency(
    definition_runtime,
):
    service, scope, principal, sessions = definition_runtime
    from app.domain.models.user import GlobalRole

    principal = principal.model_copy(update={"global_role": GlobalRole.ADMIN})
    auth = AuthorizationContext.system("execution-kernel")
    one, two = manifest(1), manifest(2)
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        store = PostgresDefinitionStore(db)
        await store.seed_head(one)
        await store.register(two)
        await store.heartbeat(capability(two), ttl=timedelta(seconds=60))
        await db.commit()
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from tests.app.execution_test_support import run_policy_snapshot_json

    run_id = uuid4()
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=sessions, authorization=auth, aggregates={"run": RunAggregate()}
    )

    async def command(name, payload, version=1):
        result = await handler.handle(
            CommandEnvelope(
                command_id=uuid4(),
                command_type=name,
                command_schema_version=version,
                stream_type="run",
                stream_id=str(run_id),
                owner_user_id=principal.user_id,
                team_id=None,
                correlation_id=run_id,
                causation_id=None,
                issued_at=datetime.now(UTC),
                payload=payload,
            )
        )
        assert result.status == "accepted", result

    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "admin-drain",
            "source_entity_id": "owned-version-test",
            "semantic_payload": {},
            "policy_snapshot": run_policy_snapshot_json("agent"),
            "definition_pin": one.pin.model_dump(mode="json"),
            "definition_manifest": one.model_dump(mode="json"),
        },
        3,
    )
    user = AuthorizationContext.for_principal(principal, scope=scope)
    api_sessions = service.uow_factory(user).session_factory
    ingress = PostgresDefinitionManagement(session_factory=api_sessions, authorization=user)
    worker = PostgresDefinitionManagement(session_factory=sessions, authorization=auth)
    management = DefinitionManagementService(ingress, wait_seconds=0)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: principal
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        principal=principal, scope=scope
    )
    app.dependency_overrides[get_definition_management_service] = lambda: management
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://owned-test"
    ) as client:
        listing = await client.get("/admin/execution-definitions?limit=1")
        assert listing.status_code == 200, listing.text
        assert listing.headers["cache-control"] == "no-store"
        assert len(listing.json()["data"]["items"]) == 1
        assert listing.json()["data"]["next_offset"] == 1
        assert (await client.get("/admin/execution-workers")).json()["data"]["items"][0][
            "available"
        ]
        body = {
            "request_id": str(uuid4()),
            "expected_digest": one.digest,
            "target_version": 2,
            "reason": "published for owned test",
        }
        path = "/admin/execution-definitions/agent/head"
        pending = await client.post(path, json=body)
        assert pending.status_code == 202, pending.text
        assert (await client.post(path, json=body)).status_code == 202
        assert (await client.post(path, json={**body, "reason": "changed"})).status_code == 409
        assert (await worker.process_pending())["emitted"] == 1
        complete = await client.post(path, json=body)
        assert complete.status_code == 200, complete.text
        assert complete.json()["data"]["result"]["pin"]["definition_version"] == 2
        assert (await worker.process_pending())["emitted"] == 0
        retired = {
            "request_id": str(uuid4()),
            "expected_manifest_digest": one.digest,
            "reason": "drained",
        }
        assert (
            await client.post("/admin/execution-definitions/agent/1/retire", json=retired)
        ).status_code == 202
        assert (await worker.process_pending())["emitted"] == 1
        denied = await client.post("/admin/execution-definitions/agent/1/retire", json=retired)
        assert denied.status_code == 409, denied.text
        assert denied.json()["data"]["references"]["counts"]["active_runs"] == 1
        assert denied.json()["data"]["references"]["run_ids"] == [str(run_id)]
        await command("CancelRun", {"reason": "drain old definition"})
        # A completed rejection is immutable. A new administrative request retries after drain.
        assert (
            await client.post("/admin/execution-definitions/agent/1/retire", json=retired)
        ).status_code == 409
        retired = {**retired, "request_id": str(uuid4())}
        assert (
            await client.post("/admin/execution-definitions/agent/1/retire", json=retired)
        ).status_code == 202
        await worker.process_pending()
        assert (
            await client.post("/admin/execution-definitions/agent/1/retire", json=retired)
        ).status_code == 200
        blocked = {
            "request_id": str(uuid4()),
            "expected_manifest_digest": two.digest,
            "reason": "current cannot retire",
        }
        assert (
            await client.post("/admin/execution-definitions/agent/2/retire", json=blocked)
        ).status_code == 202
        await worker.process_pending()
        assert (
            await client.get(f"/admin/execution-definition-requests/{blocked['request_id']}")
        ).status_code == 409
        assert (
            await client.post("/admin/execution-definitions/agent/0/retire", json=retired)
        ).status_code == 422
    async with api_sessions() as db:
        await configure_session_authorization(db, user)
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE execution_definition_requests SET status='completed'"))
    async with api_sessions() as db:
        await configure_session_authorization(db, user)
        with pytest.raises(DBAPIError):
            await db.execute(text("UPDATE execution_definition_heads SET revision=revision+1"))
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        assert await db.scalar(text("SELECT count(*) FROM execution_definition_changes")) == 2
        assert await db.scalar(
            text("SELECT retired_at FROM execution_definitions WHERE definition_version=1")
        )


async def test_queued_definition_change_rechecks_actor_and_rejects_nonadmin_ingress(
    definition_runtime,
):
    from app.application.execution.definition_management import DefinitionChange
    from app.domain.errors import ForbiddenError
    from app.domain.models.user import GlobalRole

    service, scope, principal, sessions = definition_runtime
    principal = principal.model_copy(update={"global_role": GlobalRole.ADMIN})
    auth = AuthorizationContext.system("execution-kernel")
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await PostgresDefinitionStore(db).seed_head(manifest())
        await db.commit()
    user_auth = AuthorizationContext.for_principal(principal, scope=scope)
    api_sessions = service.uow_factory(user_auth).session_factory
    ingress = PostgresDefinitionManagement(session_factory=api_sessions, authorization=user_auth)
    change = DefinitionChange(
        request_id=uuid4(),
        kind="retire",
        family="agent",
        target_version=1,
        expected_digest=manifest().digest,
        reason="queued before role removal",
    )
    assert (await ingress.submit(principal.user_id, change))["status"] == "pending"
    async with sessions() as db:
        await configure_session_authorization(db, auth)
        await db.execute(
            text("UPDATE users SET global_role='user' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    worker = PostgresDefinitionManagement(session_factory=sessions, authorization=auth)
    await worker.process_pending()
    row = await worker.result(change.request_id)
    assert row["status"] == "failed"
    assert row["result"]["code"] == "DEFINITION_ACTOR_REVOKED"
    ordinary = principal.model_copy(update={"global_role": GlobalRole.USER})
    nonadmin = PostgresDefinitionManagement(
        session_factory=api_sessions,
        authorization=AuthorizationContext.for_principal(ordinary, scope=scope),
    )
    with pytest.raises(ForbiddenError):
        await DefinitionManagementService(nonadmin).change(ordinary, change)
    with pytest.raises(DBAPIError):
        await nonadmin.submit(ordinary.user_id, change.model_copy(update={"request_id": uuid4()}))
    assert (await nonadmin.workers(limit=10, offset=0))["items"] == []
