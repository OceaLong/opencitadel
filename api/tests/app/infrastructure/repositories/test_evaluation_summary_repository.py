"""Safe fixed cost/score cuts through an ordinary API connection."""

# ruff: noqa: F401,F811
import pytest
from sqlalchemy import text

from app.domain.models.authorization import AuthorizationContext
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
from tests.app.infrastructure.repositories.test_evaluation_judge_repository import (
    team_judge_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_review_repository import review_setup

pytestmark = pytest.mark.asyncio


async def test_capture_is_current_authorized_safe_and_fixed(budget_binding_fixture):
    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    await service.append_score(scope, principal, candidate.result_id, 0, "summary-human", payload)
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        snapshot = await work.evaluation_summary.capture(
            scope,
            principal,
            batch.id,
            source="human",
            dimension="correctness",
            rubric_id=payload.rubric_version,
            evaluation_revision=1,
        )
        assert snapshot["evaluation_revision"] == 1
        assert snapshot["rows"][0]["value"] == 3
        assert "private review reason" not in str(snapshot)
        assert "principal" not in str(snapshot)
        assert snapshot["captured_at"]
        assert snapshot["expires_at"]
        await work.commit()
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        replay = await work.evaluation_summary.get(scope, principal, batch.id, snapshot["id"])
        assert replay == snapshot
        assert await work.db_session.scalar(
            text(
                "SELECT NOT has_table_privilege(current_user,'evaluation_judge_invalidations','SELECT')"
            )
        )
        assert await work.db_session.scalar(
            text(
                "SELECT NOT has_table_privilege(current_user,'evaluation_summary_snapshots','INSERT')"
            )
        )


async def test_capture_rejects_unbound_rubric(budget_binding_fixture):
    from uuid import uuid4

    service, scope, principal, batch, _, _ = await review_setup(budget_binding_fixture)
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(ValueError, match="summary_query_unavailable"):
            await work.evaluation_summary.capture(
                scope,
                principal,
                batch.id,
                source="human",
                dimension="correctness",
                rubric_id=uuid4(),
            )


async def test_late_physical_settlement_keeps_prior_cost_snapshot(budget_binding_fixture):
    from app.domain.evaluation.budget import BudgetSettlement
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        late_physical_case,
    )
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import repository

    _service, _, batch, _, call, demand = await late_physical_case(
        budget_binding_fixture, "succeeded"
    )
    suites, scope, principal, suite, *tail = budget_binding_fixture

    async def capture():
        async with suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            result = await work.evaluation_summary.capture(
                scope,
                principal,
                batch.id,
                source="model",
                dimension="correctness",
                rubric_id=suite.rubric_version,
            )
            await work.commit()
            return result

    before = await capture()
    assert before["rows"][0]["subject_usage"]["tokens"] is None
    assert before["rows"][0]["subject_usage"]["unresolved"] == 1
    async with tail[-1](AuthorizationContext.system("execution-kernel")) as work:
        await repository(work).settle(call, demand, BudgetSettlement(tokens=8))
        await work.commit()
    after = await capture()
    assert after["rows"][0]["subject_usage"]["tokens"] == 8
    assert after["rows"][0]["subject_usage"]["money"] is None
    assert after["usage_watermark"] != before["usage_watermark"]
    async with suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await work.evaluation_summary.get(scope, principal, batch.id, before["id"]) == before


async def test_snapshot_eviction_explicit_reset_and_current_auth(budget_binding_fixture):
    from sqlalchemy.exc import DBAPIError

    from app.domain.evaluation.errors import DatasetConflict
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, batch, _, payload = await review_setup(budget_binding_fixture)
    snapshots = []
    for _ in range(21):
        async with service.suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            snapshots.append(
                await work.evaluation_summary.capture(
                    scope,
                    principal,
                    batch.id,
                    source="human",
                    dimension="correctness",
                    rubric_id=payload.rubric_version,
                )
            )
            await work.commit()
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(DatasetConflict, match="refresh"):
            await work.evaluation_summary.get(scope, principal, batch.id, snapshots[0]["id"])
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert (
            await work.evaluation_summary.get(scope, principal, batch.id, snapshots[1]["id"])
            == snapshots[1]
        )
        with pytest.raises(DBAPIError, match="summary_authorization_invalid"):
            await work.db_session.execute(
                text("SELECT public.opencitadel_e11_snapshot('{}',:signature)"),
                {"signature": "0" * 64},
            )
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
            {"id": principal.user_id},
        )
        await db.commit()
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(PermissionError):
            await work.evaluation_summary.get(scope, principal, batch.id, snapshots[-1]["id"])


async def test_summary_cursor_bound_and_paged_score_revision(budget_binding_fixture):
    from app.application.evaluation.summary_service import SummaryService

    service, scope, principal, batch, _, payload = await review_setup(budget_binding_fixture)
    read = SummaryService(service.suites)
    page = await read.summary(
        scope,
        principal,
        batch.id,
        source="human",
        dimension="correctness",
        rubric_id=payload.rubric_version,
        limit=1,
    )
    assert len(page.items) == 1
    assert page.evaluation_revision == 0
    assert page.distribution_metadata.numerator is None
    assert page.distribution_metadata.denominator is None
    assert page.distribution_metadata.sample_count == 0
    assert page.distribution_metadata.missing_count == 1
    assert page.distribution_metadata.excluded_count == 0
    assert page.distribution_metadata.grain == "case_config"
    assert page.quality_cost_metadata.grain == "case_result"
    assert page.quality_cost_metadata.watermark == page.usage_watermark
    context = read._context(
        scope,
        principal,
        "summary",
        str(batch.id),
        "human",
        "correctness",
        str(payload.rubric_version),
        None,
        None,
    )
    cursor = read._cursor(context, [str(page.snapshot_id), 0])
    replay = await read.summary(
        scope,
        principal,
        batch.id,
        source="human",
        dimension="correctness",
        rubric_id=payload.rubric_version,
        cursor=cursor,
        limit=1,
    )
    assert replay.snapshot_id == page.snapshot_id
    with pytest.raises(ValueError, match="invalid_cursor"):
        await read.summary(
            scope,
            principal,
            batch.id,
            source="model",
            dimension="correctness",
            rubric_id=payload.rubric_version,
            cursor=cursor,
            limit=1,
        )
    with pytest.raises(ValueError, match="invalid_cursor"):
        await read.summary(
            scope,
            principal,
            batch.id,
            source="human",
            dimension="correctness",
            rubric_id=payload.rubric_version,
            cursor=cursor + "changed",
            limit=1,
        )


async def test_cache_eviction_cannot_cross_caller_or_scope(team_judge_fixture):
    from app.domain.evaluation.errors import DatasetConflict, DatasetNotFound
    from app.domain.models.scope import OwnerScope

    fixture, reviewer = team_judge_fixture
    service, scope, principal, batch, _, payload = await review_setup(fixture)
    reviewer_scope = scope.model_copy(update={"user_id": reviewer.user_id})

    async def capture(actor, view_scope):
        async with service.suites.uow_factory(
            AuthorizationContext.for_principal(actor, scope=view_scope)
        ) as work:
            saved = await work.evaluation_summary.capture(
                view_scope,
                actor,
                batch.id,
                source="human",
                dimension="correctness",
                rubric_id=payload.rubric_version,
            )
            await work.commit()
            return saved

    kept = await capture(reviewer, reviewer_scope)
    for _ in range(21):
        await capture(principal, scope)
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(reviewer, scope=reviewer_scope)
    ) as work:
        assert (
            await work.evaluation_summary.get(reviewer_scope, reviewer, batch.id, kept["id"])
            == kept
        )
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(DatasetConflict):
            await work.evaluation_summary.get(scope, principal, batch.id, kept["id"])
    personal = OwnerScope.personal(reviewer.user_id)
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(reviewer, scope=personal)
    ) as work:
        with pytest.raises(DatasetNotFound):
            await work.evaluation_summary.get(personal, reviewer, batch.id, kept["id"])


async def test_expired_cache_resets_and_kernel_cleanup_preserves_live(budget_binding_fixture):
    import json
    from uuid import uuid4

    from app.domain.evaluation.errors import DatasetConflict
    from app.infrastructure.repositories.db_evaluation_dataset_repository import params
    from app.infrastructure.security.db_authorization import configure_session_authorization
    from core.config import load_deployment_settings
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, batch, _, payload = await review_setup(budget_binding_fixture)
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        kept = await work.evaluation_summary.capture(
            scope,
            principal,
            batch.id,
            source="human",
            dimension="correctness",
            rubric_id=payload.rubric_version,
        )
        await work.commit()
    expired = uuid4()
    # Seed only disposable cache expiry. No source, event, migration or permission is changed.
    async with execution_admin_session() as db:
        await configure_session_authorization(
            db,
            AuthorizationContext.for_principal(principal, scope=scope),
            signing_secret=load_deployment_settings().database_authorization_signing_secret,
        )
        inserted = await db.execute(
            text(
                "INSERT INTO evaluation_summary_snapshots(id,scope_key,caller_id,batch_id,expires_at,body) VALUES(:id,:scope,:actor,:batch,clock_timestamp()-interval '1 second',CAST(:body AS jsonb))"
            ),
            params(scope, id=expired, batch=batch.id, body=json.dumps(kept)),
        )
        assert inserted.rowcount == 1
        await db.commit()
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(DatasetConflict, match="refresh"):
            await work.evaluation_summary.get(scope, principal, batch.id, expired)
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_summary.cleanup_expired(limit=1) == 1
        assert await work.evaluation_summary.cleanup_expired(limit=1) == 0
        await work.commit()
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        assert await work.evaluation_summary.get(scope, principal, batch.id, kept["id"]) == kept


async def test_exact_model_invalidation_respects_historical_cut_and_new_rubric(
    budget_binding_fixture,
):
    from tests.app.infrastructure.repositories.test_evaluation_judge_repository import (
        test_rescore_new_rubric_preserves_history_and_terminal_execution,
    )

    # Actual admitted Runs and persisted source sets; the helper isolates the observer's
    # exact invalidation association with its documented unsafe detector seam.
    await test_rescore_new_rubric_preserves_history_and_terminal_execution(budget_binding_fixture)
    suites, scope, principal, suite, *tail = budget_binding_fixture
    async with tail[-1](AuthorizationContext.system("execution-kernel")) as work:
        record = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT batch_id,rubric_revision FROM evaluation_score_sets WHERE source='model' AND evaluation_revision=3"
                    )
                )
            )
            .mappings()
            .one()
        )
        batch_id = record["batch_id"]
        revised = record["rubric_revision"]

    async def capture(rubric, revision):
        async with suites.uow_factory(
            AuthorizationContext.for_principal(principal, scope=scope)
        ) as work:
            saved = await work.evaluation_summary.capture(
                scope,
                principal,
                batch_id,
                source="model",
                dimension="correctness",
                rubric_id=rubric,
                evaluation_revision=revision,
            )
            await work.commit()
            return saved["rows"][0]

    historical = await capture(suite.rubric_version, 3)
    current = await capture(suite.rubric_version, 4)
    replacement = await capture(revised, 4)
    assert historical["value"] == 4
    assert not historical["invalidated"]
    assert current["value"] is None
    assert current["invalidated"]
    assert replacement["value"] == 4
    assert not replacement["invalidated"]
