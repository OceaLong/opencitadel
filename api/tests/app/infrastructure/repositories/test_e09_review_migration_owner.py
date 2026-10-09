"""NOBYPASS function owner plus real ordinary API member credentials."""

# ruff: noqa: F401,F811
import pytest

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
from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
    test_member_append_is_atomic_idempotent_and_private_tables_stay_private as check_human,
)
from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
    test_rescore_durable_command_real_consumer_and_immediate_cancel_fence as check_rescore,
)
from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
    test_team_reviewer_and_rescore_keep_actual_authorizer as check_team,
)
from tests.app.interfaces.endpoints.test_evaluation_review_routes import (
    test_rescore_receipt_revalidates_fixed_source_before_acceptance as check_source,
)

pytestmark = pytest.mark.asyncio


async def test_non_bypass_human_command_owner(budget_binding_fixture):
    await check_human(budget_binding_fixture)


async def test_non_bypass_rescore_cancel_owner(budget_binding_fixture):
    await check_rescore(budget_binding_fixture)


async def test_non_bypass_team_source_and_actual_actor(team_judge_fixture):
    await check_team(team_judge_fixture)


@pytest.mark.parametrize("revocation", ["original_member", "source"])
async def test_non_bypass_revoked_source_receipt(team_judge_fixture, revocation, monkeypatch):
    await check_source(team_judge_fixture, revocation, monkeypatch)


async def test_non_bypass_rejects_forged_source_and_cross_scope(team_judge_fixture, monkeypatch):
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.models.scope import OwnerScope
    from app.infrastructure.repositories.db_evaluation_review_repository import (
        DBEvaluationReviewRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
        rescore_setup,
    )

    fixture, reviewer = team_judge_fixture
    service, _, scope, _, candidate, request = await rescore_setup(fixture)
    with pytest.raises(DatasetNotFound):
        await service.rescore(
            OwnerScope.personal(reviewer.user_id),
            reviewer,
            candidate.result_id,
            request,
            "foreign-source",
        )
    command = DBEvaluationReviewRepository.command

    async def forged(self, payload):
        payload = {**payload, "source_principal": reviewer.model_dump(mode="json")}
        return await command(self, payload)

    monkeypatch.setattr(DBEvaluationReviewRepository, "command", forged)
    with pytest.raises(PermissionError):
        await service.rescore(
            scope.model_copy(update={"user_id": reviewer.user_id}),
            reviewer,
            candidate.result_id,
            request,
            "forged-source",
        )


async def test_non_bypass_source_revoked_at_final_commit(team_judge_fixture, monkeypatch):
    from sqlalchemy import text

    from app.infrastructure.repositories.db_evaluation_review_repository import (
        DBEvaluationReviewRepository,
    )
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_review_repository import (
        rescore_setup,
    )

    fixture, reviewer = team_judge_fixture
    service, _, scope, original, candidate, request = await rescore_setup(fixture)
    command = DBEvaluationReviewRepository.command

    async def revoke_before_definer(self, payload):
        async with execution_admin_session() as db:
            await db.execute(
                text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
                {"team": scope.team_id, "user": original.user_id},
            )
            await db.commit()
        return await command(self, payload)

    monkeypatch.setattr(DBEvaluationReviewRepository, "command", revoke_before_definer)
    with pytest.raises(PermissionError):
        await service.rescore(
            scope.model_copy(update={"user_id": reviewer.user_id}),
            reviewer,
            candidate.result_id,
            request,
            "revoked-at-definer",
        )
