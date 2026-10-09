# ruff: noqa: F401,F811
import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_judge_repository import (
    team_judge_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_real_member_routes_cas_history_anti_enumeration_auditor(budget_binding_fixture):
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_review_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_review_service

    service, scope, principal, _batch, candidate, score = await review_setup(budget_binding_fixture)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_review_service] = lambda: service
    payload = {**score.model_dump(mode="json"), "request_id": "route-one", "expected_revision": 0}
    path = f"/evaluation/results/{candidate.result_id}/scores"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(path, json=payload)
        assert response.status_code == 200, response.text
        assert (await client.post(path, json=payload)).json() == response.json()
        conflict = await client.post(path, json={**payload, "request_id": "different"})
        assert conflict.status_code == 409
        assert (
            await client.post(f"/evaluation/results/{uuid4()}/scores", json=payload)
        ).status_code == 404
        history = await client.get(path)
        assert len(history.json()["data"]["items"]) == 1
        assert history.json()["data"]["evaluation_revision"] == 1
        assert history.headers["cache-control"] == "no-store"
        assert (
            await client.get("/evaluation/reviews", params={"status": "all"})
        ).status_code == 200
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=scope, principal=principal.model_copy(update={"global_role": "auditor"})
        )
        assert (
            await client.post(path, json={**payload, "request_id": "auditor"})
        ).status_code == 403


async def test_real_http_rescore_and_cancel_flow(budget_binding_fixture):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_review_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_review_service
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
        rescore_setup,
    )

    service, judge, scope, principal, candidate, request = await rescore_setup(
        budget_binding_fixture
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_review_service] = lambda: service
    consumer = ReviewCommandConsumer(budget_binding_fixture[-1], judge)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        path = f"/evaluation/results/{candidate.result_id}/commands/rescore"
        payload = {**request.model_dump(mode="json"), "request_id": "http-rescore"}
        accepted = await client.post(path, json=payload)
        assert accepted.status_code == 202, accepted.text
        data = accepted.json()["data"]
        assert data["status"] == "queued"
        assert data["judge_run_id"] is None
        assert (await client.post(path, json={**payload, "token_budget": 2000})).status_code == 409
        await consumer.tick()
        result = (await client.get(f"/evaluation/reviews/commands/{data['id']}")).json()["data"]
        assert result["status"] == "submitted"
        cancel = await client.post(
            f"/evaluation/results/{candidate.result_id}/commands/cancel-judge",
            json={
                "request_id": "http-cancel",
                "judge_run_id": result["judge_run_id"],
                "expected_revision": 0,
                "expected_result_revision": candidate.result_revision,
            },
        )
        assert cancel.status_code == 202, cancel.text
        assert cancel.json()["data"]["status"] == "queued"
        assert (
            await client.post(
                f"/evaluation/results/{candidate.result_id}/commands/cancel-judge",
                json={
                    "request_id": "http-cancel",
                    "judge_run_id": result["judge_run_id"],
                    "expected_revision": 0,
                    "expected_result_revision": candidate.result_revision + 1,
                },
            )
        ).status_code == 409
        assert (
            await client.post(
                f"/evaluation/results/{candidate.result_id}/commands/cancel-judge",
                json={
                    "request_id": "wrong-run",
                    "judge_run_id": str(candidate.run_id),
                    "expected_revision": 0,
                    "expected_result_revision": candidate.result_revision,
                },
            )
        ).status_code == 404
        await consumer.tick()
        await consumer.tick()
        assert (
            await client.get(f"/evaluation/reviews/commands/{cancel.json()['data']['id']}")
        ).json()["data"]["status"] == "cancelled"


@pytest.mark.parametrize("revocation", ["original_member", "source"])
async def test_rescore_receipt_revalidates_fixed_source_before_acceptance(
    team_judge_fixture, revocation, monkeypatch
):
    from sqlalchemy import text

    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_review_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_review_service
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
        rescore_setup,
    )

    fixture, reviewer = team_judge_fixture
    if revocation == "source":
        from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter

        file_id = str(uuid4())
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "INSERT INTO files(id,team_id,key,content_digest,object_identity) VALUES (:id,:team,'review-source',:digest,:object)"
                ),
                {"id": file_id, "team": fixture[1].team_id, "digest": "a" * 64, "object": uuid4()},
            )
            await db.commit()
        record = ExecutionContentWriter.record

        async def with_source(self, producer, **kwargs):
            if kwargs["value"].get("message", {}).get("content") == "answer":
                kwargs["attachment_ids"] = (file_id,)
            return await record(self, producer, **kwargs)

        monkeypatch.setattr(ExecutionContentWriter, "record", with_source)
    service, _, scope, original, candidate, request = await rescore_setup(fixture)
    reviewer_scope = scope.model_copy(update={"user_id": reviewer.user_id})
    async with execution_admin_session() as db:
        if revocation == "original_member":
            await db.execute(
                text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
                {"team": scope.team_id, "user": original.user_id},
            )
        else:
            await db.execute(
                text("UPDATE files SET content_available=false WHERE id=:id"), {"id": file_id}
            )
        await db.commit()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=reviewer_scope, principal=reviewer
    )
    app.dependency_overrides[get_review_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/evaluation/results/{candidate.result_id}/commands/rescore",
            json={**request.model_dump(mode="json"), "request_id": "revoked-source-receipt"},
        )
        assert response.status_code == (403 if revocation == "original_member" else 409), (
            response.text
        )
    async with fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_review_commands"))
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action='evaluation.review.rescore'")
            )
            == 0
        )
