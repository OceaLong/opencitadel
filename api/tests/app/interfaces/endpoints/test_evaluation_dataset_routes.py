# ruff: noqa: F811 -- pytest fixture imports intentionally share parameter names
"""Actual routes, DTOs and PostgreSQL service; only request identity is supplied."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.app.alembic.test_execution_view_migration import isolated_database  # noqa: F401
from tests.app.application.services.test_execution_usage_postgres import (
    fresh_f07_database,  # noqa: F401
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import (
    datasets,  # noqa: F401
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_actual_routes_create_import_publish_and_fixed_read(datasets):
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_dataset_routes import router
    from app.interfaces.service_dependencies import get_dataset_service

    service, scope, principal, _, _ = datasets
    from app.interfaces.errors.exception_handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_dataset_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/evaluation/datasets",
            json={"request_id": str(uuid4()), "expected_revision": 0, "name": "HTTP"},
        )
        assert response.status_code == 200, response.text
        dataset_id = response.json()["data"]["id"]
        response = await client.post(
            f"/evaluation/datasets/{dataset_id}/imports/validate",
            data={"request_id": str(uuid4()), "expected_revision": "1"},
            files={
                "file": (
                    "cases.json",
                    b'{"schema_version":1,"cases":[{"case_key":"one","input":"question"}]}',
                    "application/json",
                )
            },
        )
        assert response.status_code == 200, response.text
        preview = response.json()["data"]
        assert not preview["errors"]
        response = await client.post(
            f"/evaluation/datasets/{dataset_id}/imports/{preview['import_id']}/apply",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "input_digest": preview["input_digest"],
            },
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            f"/evaluation/datasets/{dataset_id}/publish",
            json={"request_id": str(uuid4()), "expected_revision": 2},
        )
        assert response.status_code == 200, response.text
        version_id = response.json()["data"]["id"]
        history = await client.get(f"/evaluation/datasets/{dataset_id}/versions")
        assert history.status_code == 200, history.text
        assert history.json()["data"]["items"][0]["id"] == version_id
        assert "question" not in history.text

        response = await client.get(f"/evaluation/dataset-versions/{version_id}")
        assert response.status_code == 200
        assert response.json()["data"]["cases"][0]["input"] == "question"
        assert "storage_key" not in response.text
        assert "digest" not in response.text
        assert response.headers["cache-control"] == "no-store"
        for path in ("datasets", "dataset-versions"):
            missing = await client.get(f"/evaluation/{path}/{uuid4()}")
            assert missing.status_code == 404, missing.text
        from app.domain.models.scope import OwnerScope, Principal
        from tests.app.application.services.test_artifact_provenance_postgres import seed

        other, _ = await seed()
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=OwnerScope.personal(other), principal=Principal(user_id=other)
        )
        for path, identity in (("datasets", dataset_id), ("dataset-versions", version_id)):
            hidden = await client.get(f"/evaluation/{path}/{identity}")
            assert hidden.status_code == 404, hidden.text
        hidden_history = await client.get(f"/evaluation/datasets/{dataset_id}/versions")
        assert hidden_history.status_code == 404
        schema = app.openapi()
        assert "CaseRevision" in schema["components"]["schemas"]
        assert "source_request" not in schema["components"]["schemas"]["CaseInput"]["properties"]


@pytest.mark.parametrize("redacted", [False, True])
async def test_real_f_snapshot_to_from_run_route_preserves_prefix_and_confirmation(
    datasets, redacted
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.public_projection import PublicEventCursor
    from app.application.services.execution_content_service import ExecutionContentService
    from app.application.services.execution_event_service import ExecutionEventService
    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.execution.activity import ActivityClaim, ActivityRequest
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunAggregate
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import WorkspaceContext
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.execution.postgres_run_public_events import PostgresRunPublicEvents
    from app.infrastructure.execution.sqlalchemy_orchestrator import SqlAlchemyExecutionOrchestrator
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_dataset_routes import router
    from app.interfaces.service_dependencies import get_dataset_service
    from tests.app.execution_test_support import execution_admin_session, run_policy_snapshot_json

    service, scope, principal, objects, _ = datasets
    from app.domain.models.file import File

    dependency = File(
        owner_user_id=scope.user_id,
        content_digest="b" * 64,
        object_identity=str(uuid4()),
        key="source-dependency",
    )
    async with service.uow_factory(service._auth(scope, principal)) as uow:
        await uow.file.save(dependency)
        await uow.commit()
    session_id, run_id, activity_id = str(uuid4()), uuid4(), uuid4()
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO sessions(id,owner_user_id) VALUES (:id,:owner)"),
            {"id": session_id, "owner": scope.user_id},
        )
        await db.commit()
    kernel = AuthorizationContext.system("e01-controlled-run")
    handler = SqlAlchemyExecutionOrchestrator(
        session_factory=execution_admin_session,
        aggregates={"run": RunAggregate()},
        authorization=kernel,
    )

    async def command(kind, payload, schema=1, command_id=None):
        envelope = CommandEnvelope(
            command_id=command_id or uuid4(),
            command_type=kind,
            command_schema_version=schema,
            stream_type="run",
            stream_id=str(run_id),
            owner_user_id=scope.user_id,
            team_id=None,
            correlation_id=run_id,
            causation_id=None,
            issued_at=datetime.now(UTC),
            payload=payload,
        )
        outcome = await handler.handle(envelope)
        assert outcome.status == "accepted", outcome

    message = "actual question"
    earlier_input = "earlier input " * 6000
    context = {
        "message": message,
        "conversation": [
            {"role": "user", "content": earlier_input},
            {"role": "assistant", "content": "earlier answer"},
        ],
        "attachments": [{"file_id": dependency.id}],
        "resource_bindings": [],
    }
    await command(
        "CreateRun",
        {
            "family": "agent",
            "source_entity_type": "session",
            "source_entity_id": session_id,
            "semantic_payload": {},
            "public_input": {"role": "user", "message": message},
            "policy_snapshot": run_policy_snapshot_json("agent"),
        },
    )
    await command("StartRun", {})
    store = ActivityObjectStore(objects)
    input_ref, input_digest = await store.put_input(run_id, context)
    request = {"round": 0}
    if redacted:
        request["history_refs"] = ["execution/results/private/history.json"]
    timeout_at = datetime.now(UTC) + timedelta(hours=1)
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity_id),
            "activity_type": "model.call",
            "timeout_at": timeout_at.isoformat(),
            "input_ref": input_ref,
            "input_digest": input_digest,
            "input_payload": request,
        },
        2,
    )
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET claim_generation=1,status='call_started' WHERE activity_id=:id"
            ),
            {"id": activity_id},
        )
        await db.commit()
    claim = ActivityClaim(
        request=ActivityRequest(
            activity_id=activity_id,
            activity_type="model.call",
            aggregate_type="run",
            aggregate_id=str(run_id),
            generation=0,
            timeout_at=timeout_at,
            input_ref=input_ref,
            input_digest=input_digest,
            input_payload=request,
        ),
        claim_generation=1,
        owner_user_id=scope.user_id,
        team_id=None,
    )
    writer = ExecutionContentWriter(
        session_factory=execution_admin_session, authorization=kernel, objects=store
    )
    started_id = uuid4()
    started = {"activity_id": str(activity_id), "generation": 0, "claim_generation": 1}
    await writer.prepare(claim, started_id, "MarkActivityCallStarted", started)
    await command("MarkActivityCallStarted", started, 2, started_id)
    result_ref = await store.put_result(activity_id, {"answer": "historical candidate"})
    completed_id = uuid4()
    completed = {**started, "result_ref": result_ref, "result_summary": "not the case input"}
    await writer.prepare(claim, completed_id, "CompleteActivity", completed)
    await command("CompleteActivity", completed, 2, completed_id)
    await PostgresFormalProjector(
        session_factory=execution_admin_session, authorization=kernel
    ).run_once(scope, limit=1000)
    auth = service._auth(scope, principal)
    factory = service.uow_factory(auth).session_factory
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=factory, authorization=auth),
        cursor_secret=b"e01-real-f-view-secret",
    )
    service.views = views
    service.content = ExecutionContentService(
        lambda: service.uow_factory(auth), views, None, cursor_secret=b"e01-real-content-secret"
    )
    port = PostgresRunPublicEvents(
        session_factory=factory,
        authorization=auth,
        cursor=PublicEventCursor(secret=b"e01-real-event-secret"),
    )
    service.events = ExecutionEventService(
        port, cursor_secret=b"e01-real-event-secret", revalidate=port.revalidate
    )
    view = await views.get_view(scope, run_id)
    step = next(step for step in view.steps if step.input_ref)
    from app.interfaces.errors.exception_handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_dataset_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        initial = (
            await client.post(
                "/evaluation/datasets",
                json={"request_id": str(uuid4()), "expected_revision": 0, "name": "Run source"},
            )
        ).json()["data"]
        preview_body = {
            "expected_revision": 1,
            "run_id": str(run_id),
            "step_id": step.step_id,
            "at": view.at,
            "case_key": "from-run",
        }
        preview = await client.post(
            f"/evaluation/datasets/{initial['id']}/from-run/preview", json=preview_body
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["data"]["input"] == message
        assert not preview.json()["data"]["reference_confirmed"]
        untouched = await client.get(f"/evaluation/datasets/{initial['id']}")
        assert untouched.json()["data"]["revision"] == 1
        assert untouched.json()["data"]["cases"] == []
        stale = await client.post(
            f"/evaluation/datasets/{initial['id']}/from-run/preview",
            json=preview_body | {"expected_revision": 2},
        )
        assert stale.status_code == 409
        assert message not in stale.text
        result = await client.post(
            f"/evaluation/datasets/{initial['id']}/from-run",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "run_id": str(run_id),
                "step_id": step.step_id,
                "at": view.at,
                "case_key": "from-run",
            },
        )
        assert result.status_code == 200, result.text
        case = result.json()["data"]["cases"][0]
        assert case["input"] == message
        assert [entry["content"] for entry in case["history"]] == [earlier_input, "earlier answer"]
        assert case["source_at"] == view.at
        assert case["input_status"] == ("sanitized" if redacted else "admitted")
        assert not case["input_confirmed"]
        assert case["reference_answer"] is None
        assert not case["reference_confirmed"]
        assert "historical candidate" in case["reference_candidate"]
        assert "execution/results/private" not in result.text
        denied = await client.post(
            f"/evaluation/datasets/{initial['id']}/publish",
            json={"request_id": str(uuid4()), "expected_revision": 2},
        )
        assert denied.status_code == 400, denied.text
        # Edit/confirm through the real PATCH DTO, retaining server provenance.
        edited = await client.patch(
            f"/evaluation/datasets/{initial['id']}/cases/from-run",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 2,
                "case": {
                    "input": "explicit edited question",
                    "history": case["history"],
                    "input_confirmed": True,
                },
            },
        )
        assert edited.status_code == 200, edited.text
        saved = edited.json()["data"]["cases"][0]
        assert saved["source_run_id"] == str(run_id)
        assert saved["input_status"] == "edited"
        published = await client.post(
            f"/evaluation/datasets/{initial['id']}/publish",
            json={"request_id": str(uuid4()), "expected_revision": 3},
        )
        assert published.status_code == 200, published.text

        async with service.uow_factory(service._auth(scope, principal)) as uow:
            await uow.file.prepare_delete(dependency.id, scope=scope, force=True)
            await uow.file.delete(dependency.id, scope=scope)
            await uow.commit()
        denied = await client.get(f"/evaluation/datasets/{initial['id']}")
        assert denied.status_code == 409, denied.text
        assert earlier_input not in denied.text
        unrelated = await client.patch(
            f"/evaluation/datasets/{initial['id']}/cases/unrelated",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 4,
                "case": {"input": "unrelated"},
            },
        )
        assert unrelated.status_code == 409, unrelated.text
        assert earlier_input not in unrelated.text
        async with service.uow_factory(service._auth(scope, principal)) as uow:
            row = await uow.evaluation_dataset.get_draft(scope, initial["id"])
            assert row["revision"] == 4
            assert len(row["members"]) == 1
