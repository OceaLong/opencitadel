"""Actual HTTP summary/list authorization and durable metadata invalidation feed reads."""

# ruff: noqa: F401,F811
import httpx
import pytest
from fastapi import FastAPI

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.infrastructure.repositories.test_e06_effect_read_migration_owner import (
    fresh_f07_database,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup

pytestmark = pytest.mark.asyncio


async def test_real_scoped_http_metadata_summary_and_human_revision_events(budget_binding_fixture):
    from app.application.evaluation.batch_service import BatchService
    from app.application.evaluation.summary_service import SummaryService
    from app.domain.models.scope import OwnerScope, WorkspaceContext
    from app.interfaces.auth_dependencies import get_workspace_context
    from app.interfaces.endpoints.evaluation_summary_routes import router
    from app.interfaces.errors.exception_handlers import register_exception_handlers
    from app.interfaces.service_dependencies import get_batch_service

    service, scope, principal, batch, candidate, score = await review_setup(budget_binding_fixture)
    read = SummaryService(service.suites)
    before = await read.events(scope, principal, batch.id)
    cursor = before[-1]["cursor"] if before else None
    await service.append_score(scope, principal, candidate.result_id, 0, "summary-route", score)
    events = await read.events(scope, principal, batch.id, cursor=cursor)
    assert any(event["kind"] == "human_review_appended" for event in events)
    assert all(set(event) == {"cursor", "revision", "kind"} for event in events)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_batch_service] = lambda: BatchService(
        service.suites, preflight_factory=None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/evaluation/batches/{batch.id}/summary",
            params={
                "source": "human",
                "dimension": "correctness",
                "result_id": str(candidate.result_id),
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        data = response.json()["data"]
        assert data["selected_result"]["id"] == str(candidate.result_id)
        assert data["items"][0]["value"] == 3
        assert "private review reason" not in response.text
        assert (await client.get("/evaluation/batches")).json()["data"]["items"][0]["id"] == str(
            batch.id
        )
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=OwnerScope.personal("another-user"), principal=principal
        )
        assert (await client.get(f"/evaluation/batches/{batch.id}/summary")).status_code == 403


async def test_current_review_context_is_authoritative_beyond_history_page(budget_binding_fixture):
    from app.domain.evaluation.review import HumanReview, HumanScore
    from app.domain.models.scope import OwnerScope, WorkspaceContext
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
    score = HumanReview(
        rubric_version=request.rubric_version,
        expected_result_revision=candidate.result_revision,
        scores=(HumanScore(dimension="correctness", value=3),),
    )
    head = None
    result_revision = candidate.result_revision
    for revision in range(52):
        revised = score.model_copy(
            update={
                "expected_result_revision": result_revision,
                "scores": (
                    score.scores[0].model_copy(
                        update={"value": revision % 5, "supersedes_id": head}
                    ),
                ),
            }
        )
        receipt = await service.append_score(
            scope, principal, candidate.result_id, revision, f"current-{revision}", revised
        )
        result_revision = receipt.result_revision
        page = await service.history_page(
            scope, principal, candidate.result_id, evaluation_revision=revision + 1, limit=200
        )
        head = page.items[-1].id
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
        scope=scope, principal=principal
    )
    app.dependency_overrides[get_review_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.get(f"/evaluation/results/{candidate.result_id}/review-context")
        assert response.status_code == 200, response.text
        value = response.json()["data"]
        assert value["evaluation_revision"] == 52
        assert value["result_revision"] == result_revision
        assert value["human_heads"][0]["id"] == str(head)
        assert value["human_heads"][0]["value"] == 1
        assert "correctness" in value["applicable_dimensions"]
        assert response.headers["cache-control"] == "no-store"
        from uuid import uuid4

        from app.domain.evaluation.review import HumanReview, HumanScore
        from app.domain.evaluation.rubric import RubricDefinition, RubricDimension

        old = await service.suites.get_version(scope, principal, "rubric", score.rubric_version)
        definition = RubricDefinition(
            dimensions=(
                RubricDimension(id="quality", name="Quality", anchors=("a", "b", "c", "d", "e")),
            ),
            judge_config_version=old.judge_config_version,
        )
        draft = await service.suites.create(
            scope,
            principal,
            kind="rubric",
            name="Custom review",
            definition=definition.model_dump(mode="json"),
            request_id=str(uuid4()),
        )
        revised = await service.suites.publish(
            scope,
            principal,
            kind="rubric",
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )
        from app.application.evaluation.review_consumer import ReviewCommandConsumer

        rescore = request.model_copy(
            update={
                "rubric_version": revised.id,
                "judge_config_version": revised.judge_config_version,
                "expected_result_revision": result_revision,
                "expected_evaluation_revision": 52,
            }
        )
        await service.rescore(scope, principal, candidate.result_id, rescore, "custom-rescore")
        await ReviewCommandConsumer(budget_binding_fixture[-1], judge).tick()
        receipt = await service.append_score(
            scope,
            principal,
            candidate.result_id,
            52,
            "new-custom",
            HumanReview(
                rubric_version=revised.id,
                expected_result_revision=result_revision,
                scores=(HumanScore(dimension="quality", value=4),),
            ),
        )
        current = (
            await client.get(f"/evaluation/results/{candidate.result_id}/review-context")
        ).json()["data"]
        assert current["rubric"]["id"] == str(revised.id)
        assert current["applicable_dimensions"] == ["quality"]
        assert current["human_heads"][0]["dimension"] == "quality"
        assert current["human_heads"][0]["value"] == 4
        assert current["result_revision"] == receipt.result_revision
        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=OwnerScope.personal("other"), principal=principal
        )
        assert (
            await client.get(f"/evaluation/results/{candidate.result_id}/review-context")
        ).status_code == 403
        from sqlalchemy import text

        from tests.app.execution_test_support import execution_admin_session

        app.dependency_overrides[get_workspace_context] = lambda: WorkspaceContext(
            scope=scope, principal=principal
        )
        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
            await db.commit()
        assert (
            await client.get(f"/evaluation/results/{candidate.result_id}/review-context")
        ).status_code == 403
