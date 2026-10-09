# ruff: noqa: F401,F811
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

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
from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def review_setup(fixture):
    from app.application.evaluation.review_service import ReviewService
    from app.domain.evaluation.review import HumanReview, HumanScore

    suites, scope, principal, suite, *_tail = fixture
    _, _, batch, candidate = await completed(fixture)
    service = ReviewService(suites)
    payload = HumanReview(
        rubric_version=suite.rubric_version,
        expected_result_revision=candidate.result_revision,
        scores=(HumanScore(dimension="correctness", value=3, reason="private review reason"),),
    )
    return service, scope, principal, batch, candidate, payload


async def test_member_append_is_atomic_idempotent_and_private_tables_stay_private(
    budget_binding_fixture,
):
    from app.domain.models.authorization import AuthorizationContext

    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    receipt = await service.append_score(
        scope, principal, candidate.result_id, 0, "human-one", payload
    )
    assert receipt.evaluation_revision == 1
    assert (
        await service.append_score(scope, principal, candidate.result_id, 0, "human-one", payload)
        == receipt
    )
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert len(history) == 1
        assert history[0].author == principal.user_id
        assert history[0].score.reason == "private review reason"
        with pytest.raises(DBAPIError, match="permission denied"):
            await work.db_session.execute(text("UPDATE evaluation_score_heads SET revision=99"))
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        audits = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT actor_user_id,metadata FROM audit_logs WHERE action='evaluation.review.human'"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(audits) == 1
        assert audits[0]["actor_user_id"] == principal.user_id
        assert "private review reason" not in str(audits)
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == "running"


async def test_two_new_writes_same_revision_conflict(budget_binding_fixture):
    from app.domain.evaluation.errors import DatasetConflict

    service, scope, principal, _, candidate, payload = await review_setup(budget_binding_fixture)
    results = await asyncio.gather(
        *[
            service.append_score(scope, principal, candidate.result_id, 0, str(uuid4()), payload)
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(r, DatasetConflict) for r in results) == 1
    assert sum(not isinstance(r, Exception) for r in results) == 1


async def required_fixture(fixture):
    from app.domain.evaluation.configuration import SuiteDefinition
    from app.domain.evaluation.rubric import RequiredCondition, RubricDefinition

    suites, scope, principal, suite, *tail = fixture
    old = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    definition = RubricDefinition(
        **{k: getattr(old, k) for k in RubricDefinition.model_fields}
    ).model_copy(
        update={
            "required_conditions": (
                RequiredCondition(dimension_id="correctness", minimum=3, source="human"),
                RequiredCondition(dimension_id="completeness", minimum=3, source="human"),
            )
        }
    )
    draft = await suites.create(
        scope,
        principal,
        kind="rubric",
        name="Human required",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    rubric = await suites.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    definition = SuiteDefinition(
        **{k: getattr(suite, k) for k in SuiteDefinition.model_fields}
    ).model_copy(update={"rubric_version": rubric.id})
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Review suite",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    suite = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    return suites, scope, principal, suite, *tail


async def test_partial_review_after_completed_preserves_model_and_history(budget_binding_fixture):
    from app.domain.evaluation.review import HumanScore
    from app.domain.evaluation.scoring import ScoreValue
    from app.domain.models.authorization import AuthorizationContext

    fixture = await required_fixture(budget_binding_fixture)
    service, scope, principal, batch, candidate, payload = await review_setup(fixture)
    rubric = await service.suites.get_version(scope, principal, "rubric", payload.rubric_version)
    async with fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_score.append(
            scope,
            principal,
            candidate,
            source="model",
            scores=tuple(
                ScoreValue(
                    source="model",
                    dimension=d.id,
                    rubric_revision=rubric.id,
                    value=4,
                    status="valid",
                )
                for d in rubric.dimensions
            ),
            expected_evaluation_revision=0,
            request_id="original-model",
            required_dimensions=(),
            applicable_dimensions=tuple(d.id for d in rubric.dimensions),
        )
        await work.db_session.execute(
            text("UPDATE evaluation_batches SET status='completed' WHERE id=:id"), {"id": batch.id}
        )
        await work.commit()
    payload = payload.model_copy(update={"expected_result_revision": candidate.result_revision + 1})
    one = await service.append_score(scope, principal, candidate.result_id, 1, "one", payload)
    assert one.review_status == "pending"
    queue = await service.list_pending(scope, principal=principal)
    assert queue.items[0].result_id == candidate.result_id
    two_payload = payload.model_copy(
        update={
            "expected_result_revision": one.result_revision,
            "scores": (HumanScore(dimension="completeness", value=1),),
        }
    )
    two = await service.append_score(scope, principal, candidate.result_id, 2, "two", two_payload)
    assert two.review_status == "complete"
    assert not (await service.list_pending(scope, principal=principal)).items
    async with fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        row = await work.evaluation_batch.get(scope, batch.id)
        assert row["status"] == "completed"
        assert row["review_status"] == "complete"
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=3)
        model = [s for s in history if s.score.source == "model"]
        assert len(model) == 3
        assert all(s.score.value == 4 for s in model)
        assert len([s for s in history if s.score.source == "human"]) == 2
        old_human = next(
            s for s in history if s.score.source == "human" and s.score.dimension == "correctness"
        )
    corrected = payload.model_copy(
        update={
            "expected_result_revision": two.result_revision,
            "scores": (HumanScore(dimension="correctness", value=2, supersedes_id=old_human.id),),
        }
    )
    three = await service.append_score(
        scope, principal, candidate.result_id, 3, "correct", corrected
    )
    assert three.evaluation_revision == 4
    rejected = corrected.model_copy(
        update={
            "expected_result_revision": three.result_revision,
            "scores": (
                HumanScore(
                    dimension="correctness",
                    value=1,
                    supersedes_id=next(s.id for s in model if s.score.dimension == "correctness"),
                ),
            ),
        }
    )
    with pytest.raises(ValueError, match="review_command_invalid"):
        await service.append_score(
            scope, principal, candidate.result_id, 4, "cross-source", rejected
        )


async def test_audit_failure_rolls_back_head_score_receipt(budget_binding_fixture, monkeypatch):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_audit_repository import DBAuditRepository

    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )

    async def fail(self, log):
        if log.action == "evaluation.review.human":
            raise RuntimeError("audit storage offline")

    monkeypatch.setattr(DBAuditRepository, "add", fail)
    with pytest.raises(RuntimeError, match="audit storage offline"):
        await service.append_score(scope, principal, candidate.result_id, 0, "rollback", payload)
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 0
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_review_commands"))
            == 0
        )
        assert await work.db_session.scalar(text("SELECT count(*) FROM evaluation_scores")) == 0


async def test_queue_cursor_binds_scope_status_and_rubric(budget_binding_fixture, monkeypatch):
    from app.application.evaluation.batch_service import BatchService
    from app.domain.models.scope import OwnerScope

    start = BatchService.start

    async def unique_start(self, scope, principal, request_id, payload):
        return await start(self, scope, principal, str(uuid4()), payload)

    monkeypatch.setattr(BatchService, "start", unique_start)

    fixture = await required_fixture(budget_binding_fixture)
    service, scope, principal, _batch, _candidate, _payload = await review_setup(fixture)
    page = await service.list_pending(scope, principal=principal, limit=1)
    assert len(page.items) == 1
    assert page.next_cursor
    second = await service.list_pending(
        scope, principal=principal, limit=1, cursor=page.next_cursor
    )
    assert not second.items
    for changes in (
        {"status": "complete"},
        {"rubric_id": uuid4()},
        {"scope": OwnerScope.personal("other")},
    ):
        with pytest.raises((ValueError, PermissionError)):
            await service.list_pending(
                changes.get("scope", scope),
                principal=principal,
                cursor=page.next_cursor,
                **{k: v for k, v in changes.items() if k != "scope"},
            )


async def rescore_setup(fixture):
    from app.application.evaluation.judge_service import JudgeService
    from app.application.evaluation.review_service import ReviewService
    from app.domain.evaluation.judge_protocol import RescoreRequest
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, principal, suite, *_tail = fixture
    _, scheduler, _batch, candidate = await completed(fixture, final_output="answer")
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    judge = JudgeService(
        fixture[-1],
        suites,
        RuleEvidenceReader(fixture[-1], content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    request = RescoreRequest(
        rubric_version=rubric.id,
        judge_config_version=rubric.judge_config_version,
        expected_evaluation_revision=0,
        expected_result_revision=candidate.result_revision,
        token_budget=1000,
        money_budget=None,
    )
    return ReviewService(suites), judge, scope, principal, candidate, request


async def test_rescore_durable_command_real_consumer_and_immediate_cancel_fence(
    budget_binding_fixture,
):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.models.authorization import AuthorizationContext

    service, judge, scope, principal, candidate, request = await rescore_setup(
        budget_binding_fixture
    )
    receipt = await service.rescore(scope, principal, candidate.result_id, request, "rescore-one")
    assert receipt.status == "queued"
    assert receipt.judge_run_id is None
    consumer = ReviewCommandConsumer(budget_binding_fixture[-1], judge)
    assert await consumer.tick() == 1
    saved = await service.get_command(scope, principal, receipt.id)
    assert saved.status == "submitted"
    assert saved.judge_run_id is not None
    assert await consumer.tick() == 0
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, saved.judge_run_id)
        assert intent["authorizer"] == principal.model_dump(mode="json")
        assert intent["rescore"]["token_budget"] == 1000
        assert intent["namespace_id"] != candidate.batch_id
        await work.evaluation_judge.authorize_run(scope, saved.judge_run_id)
    cancel = await service.cancel(
        scope,
        principal,
        candidate.result_id,
        saved.judge_run_id,
        0,
        candidate.result_revision,
        "cancel-one",
    )
    assert cancel.status == "queued"
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(ValueError, match="judge_cancelled"):
            await work.evaluation_judge.authorize_run(scope, saved.judge_run_id)
    assert await consumer.tick() == 1
    assert (await service.get_command(scope, principal, cancel.id)).status == "cancelling"
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (await work.evaluation_judge.get(scope, saved.judge_run_id))["status"] == "stopped"
    await consumer.tick()
    assert (await service.get_command(scope, principal, cancel.id)).status == "cancelled"


async def test_rescore_consumption_rechecks_revision_and_revoked_requester(budget_binding_fixture):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.models.authorization import AuthorizationContext

    service, judge, scope, principal, candidate, request = await rescore_setup(
        budget_binding_fixture
    )
    receipt = await service.rescore(scope, principal, candidate.result_id, request, "stale")
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.db_session.execute(
            text("UPDATE evaluation_batch_results SET revision=revision+1 WHERE id=:id"),
            {"id": candidate.result_id},
        )
        await work.commit()
    assert await ReviewCommandConsumer(budget_binding_fixture[-1], judge).tick() == 1
    saved = await service.get_command(scope, principal, receipt.id)
    assert saved.status == "failed"
    assert saved.error == "review_revision_conflict"
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_judge_intents")) == 0
        )


async def test_automatic_explicit_predecessor_cannot_cross_source(budget_binding_fixture):
    from app.domain.evaluation.scoring import ScoreValue
    from app.domain.models.authorization import AuthorizationContext

    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    receipt = await service.append_score(scope, principal, candidate.result_id, 0, "human", payload)
    rubric = await service.suites.get_version(scope, principal, "rubric", payload.rubric_version)
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        with pytest.raises(ValueError, match="score_supersedes_mismatch"):
            await work.evaluation_score.append(
                scope,
                principal,
                candidate.model_copy(update={"result_revision": receipt.result_revision}),
                source="model",
                scores=tuple(
                    ScoreValue(
                        source="model",
                        dimension=d.id,
                        value=3,
                        rubric_revision=rubric.id,
                        status="valid",
                    )
                    for d in rubric.dimensions
                ),
                expected_evaluation_revision=1,
                request_id="cross-source",
                required_dimensions=(),
                applicable_dimensions=tuple(d.id for d in rubric.dimensions),
                supersedes={"correctness": history[0].id},
            )


async def test_batch_start_cancel_commands_have_single_atomic_real_actor_audit(
    budget_binding_fixture,
):
    from app.domain.models.authorization import AuthorizationContext

    service, scope, principal, batch, _candidate, _payload = await review_setup(
        budget_binding_fixture
    )
    from app.application.evaluation.batch_service import BatchService

    batches = BatchService(service.suites, preflight_factory=None)
    await batches.cancel(scope, principal, "audit-cancel", {"batch_id": str(batch.id)})
    await batches.cancel(scope, principal, "audit-cancel", {"batch_id": str(batch.id)})
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        rows = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT action,actor_user_id,metadata FROM audit_logs WHERE action IN ('evaluation.batch.start','evaluation.batch.cancel')"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert {r["action"] for r in rows} == {"evaluation.batch.start", "evaluation.batch.cancel"}
        assert len(rows) == 2
        assert all(r["actor_user_id"] == principal.user_id for r in rows)


async def test_team_reviewer_and_rescore_keep_actual_authorizer(team_judge_fixture):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.evaluation.review import HumanReview, HumanScore
    from app.domain.models.authorization import AuthorizationContext

    fixture, caller = team_judge_fixture
    service, judge, scope, original, candidate, request = await rescore_setup(fixture)
    caller_scope = scope.model_copy(update={"user_id": caller.user_id})
    human = HumanReview(
        rubric_version=request.rubric_version,
        expected_result_revision=candidate.result_revision,
        scores=(HumanScore(dimension="correctness", value=2),),
    )
    from app.domain.evaluation.errors import DatasetNotFound
    from app.domain.models.scope import OwnerScope

    personal = OwnerScope.personal(original.user_id)
    with pytest.raises(DatasetNotFound):
        await service.append_score(personal, original, candidate.result_id, 0, "cross-scope", human)
    assert not (await service.list_pending(personal, principal=original, status="all")).items
    saved = await service.append_score(
        caller_scope, caller, candidate.result_id, 0, "team-human", human
    )
    request = request.model_copy(
        update={
            "expected_evaluation_revision": 1,
            "expected_result_revision": saved.result_revision,
        }
    )
    command = await service.rescore(
        caller_scope, caller, candidate.result_id, request, "team-added-budget"
    )
    assert await ReviewCommandConsumer(fixture[-1], judge).tick() == 1
    command = await service.get_command(caller_scope, caller, command.id)
    assert command.status == "submitted"
    async with fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, command.judge_run_id)
        assert intent["authorizer"]["user_id"] == caller.user_id
        assert (await work.evaluation_batch.get(scope, candidate.batch_id))["principal"][
            "user_id"
        ] == original.user_id
        scores = await work.evaluation_score.history(
            scope, candidate.batch_id, evaluation_revision=1
        )
        assert scores[0].author == caller.user_id


@pytest.mark.parametrize("kind", ["human", "rescore"])
async def test_revoked_team_member_cannot_mutate_or_consume(team_judge_fixture, kind):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.evaluation.review import HumanReview, HumanScore
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.execution_test_support import execution_admin_session

    fixture, caller = team_judge_fixture
    service, judge, scope, principal, candidate, request = await rescore_setup(fixture)
    caller_scope = scope.model_copy(update={"user_id": caller.user_id})
    if kind == "rescore":
        command = await service.rescore(
            caller_scope, caller, candidate.result_id, request, "revoke-budget"
        )
    async with execution_admin_session() as db:
        await db.execute(
            text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
            {"team": scope.team_id, "user": caller.user_id},
        )
        await db.commit()
    if kind == "human":
        with pytest.raises(PermissionError):
            await service.append_score(
                caller_scope,
                caller,
                candidate.result_id,
                0,
                "revoked",
                HumanReview(
                    rubric_version=request.rubric_version,
                    expected_result_revision=candidate.result_revision,
                    scores=(HumanScore(dimension="correctness", value=3),),
                ),
            )
    else:
        assert await ReviewCommandConsumer(fixture[-1], judge).tick() == 1
        saved = await service.get_command(scope, principal, command.id)
        assert saved.status == "failed"
        assert saved.error == "review_authority_unavailable"
    async with fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_judge_intents")) == 0
        )
        assert await work.evaluation_score.revision(scope, candidate.batch_id) == 0


async def test_recover_claim_after_judge_admission_commit_is_idempotent(
    budget_binding_fixture, monkeypatch
):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_review_repository import (
        DBEvaluationReviewRepository,
    )

    service, judge, scope, principal, candidate, request = await rescore_setup(
        budget_binding_fixture
    )
    receipt = await service.rescore(
        scope, principal, candidate.result_id, request, "recover-budget"
    )
    finish = DBEvaluationReviewRepository.finish

    async def crash(*args, **kwargs):
        raise RuntimeError("worker died after admit")

    monkeypatch.setattr(DBEvaluationReviewRepository, "finish", crash)
    consumer = ReviewCommandConsumer(budget_binding_fixture[-1], judge)
    with pytest.raises(RuntimeError):
        await consumer.tick()
    monkeypatch.setattr(DBEvaluationReviewRepository, "finish", finish)
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.db_session.execute(
            text(
                "UPDATE evaluation_review_commands SET claim_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": receipt.id},
        )
        await work.commit()
    assert await consumer.tick() == 1
    saved = await service.get_command(scope, principal, receipt.id)
    assert saved.status == "submitted"
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_judge_intents")) == 1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action='evaluation.rescore.authorize'")
            )
            == 1
        )


async def test_rescore_new_rubric_keeps_old_heads_and_current_review_queue(budget_binding_fixture):
    import json
    from types import SimpleNamespace

    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.evaluation.review import HumanReview, HumanScore
    from app.domain.evaluation.rubric import RequiredCondition, RubricDefinition
    from app.domain.models.authorization import AuthorizationContext
    from tests.app.infrastructure.repositories.test_evaluation_judge_repository import finish_judge

    service, judge, scope, principal, candidate, request = await rescore_setup(
        budget_binding_fixture
    )
    old = await service.suites.get_version(scope, principal, "rubric", request.rubric_version)
    human = await service.append_score(
        scope,
        principal,
        candidate.result_id,
        0,
        "old-human",
        HumanReview(
            rubric_version=old.id,
            expected_result_revision=candidate.result_revision,
            scores=(HumanScore(dimension="correctness", value=3),),
        ),
    )
    definition = RubricDefinition(
        dimensions=(old.dimensions[0],),
        required_conditions=(
            RequiredCondition(dimension_id="correctness", minimum=3, source="human"),
        ),
        judge_config_version=old.judge_config_version,
    )
    draft = await service.suites.create(
        scope,
        principal,
        kind="rubric",
        name="New review rubric",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    new = await service.suites.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    request = request.model_copy(
        update={
            "rubric_version": new.id,
            "expected_evaluation_revision": 1,
            "expected_result_revision": human.result_revision,
        }
    )
    command = await service.rescore(scope, principal, candidate.result_id, request, "new-rubric")
    consumer = ReviewCommandConsumer(budget_binding_fixture[-1], judge)
    await consumer.tick()
    command = await service.get_command(scope, principal, command.id)
    assert command.status == "submitted"
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, command.judge_run_id)
    await finish_judge(
        budget_binding_fixture,
        SimpleNamespace(execution_policy=judge.execution_policy),
        intent,
        json.dumps(
            {
                "status": "complete",
                "dimensions": [
                    {"name": "correctness", "score": 4, "reason": "match", "evidence": []}
                ],
                "unavailable_reason": None,
            }
        ),
    )
    assert await judge.reconcile_batch(scope, candidate.batch_id) == 1
    await consumer.tick()
    assert (await service.get_command(scope, principal, command.id)).status == "completed"
    queue = await service.list_pending(scope, principal=principal)
    assert len(queue.items) == 1
    assert queue.items[0].rubric_version == new.id
    assert queue.items[0].received_dimensions == ()
    pending = queue.items[0]
    await service.append_score(
        scope,
        principal,
        candidate.result_id,
        pending.evaluation_revision,
        "new-human",
        HumanReview(
            rubric_version=new.id,
            expected_result_revision=pending.result_revision,
            scores=(HumanScore(dimension="correctness", value=2),),
        ),
    )
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        revision = await work.evaluation_score.revision(scope, candidate.batch_id)
        heads = await work.evaluation_score.heads(
            scope, candidate.batch_id, evaluation_revision=revision
        )
        humans = [s for s in heads if s.score.source == "human"]
        assert {s.score.rubric_revision for s in humans} == {old.id, new.id}
        assert all(s.supersedes_id is None for s in humans)


async def test_completed_result_in_cancelled_batch_is_reviewable(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext

    service, scope, principal, batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.evaluation_batch.submit(
            scope, principal, "cancel", "cancel-original", {}, batch.id
        )
        await work.db_session.execute(
            text(
                "UPDATE evaluation_batches SET status='cancelled',cancel_requested=true WHERE id=:id"
            ),
            {"id": batch.id},
        )
        await work.commit()
    receipt = await service.append_score(
        scope, principal, candidate.result_id, 0, "late-human", payload
    )
    assert receipt.evaluation_revision == 1
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == "cancelled"
        await work.db_session.execute(
            text("UPDATE evaluation_batch_results SET execution_status='cancelled' WHERE id=:id"),
            {"id": candidate.result_id},
        )
        await work.commit()
    with pytest.raises(ValueError, match="review_command_invalid"):
        await service.append_score(
            scope,
            principal,
            candidate.result_id,
            1,
            "cancelled-result",
            payload.model_copy(update={"expected_result_revision": receipt.result_revision}),
        )


async def test_unsigned_command_and_auditor_cannot_write_database(budget_binding_fixture):
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.user import GlobalRole
    from tests.app.execution_test_support import execution_admin_session

    service, scope, principal, _batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        with pytest.raises(DBAPIError, match="review_authorization_invalid"):
            await work.db_session.execute(
                text("SELECT public.opencitadel_e09_command('{}',:signature)"),
                {"signature": "0" * 64},
            )
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='auditor' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    auditor = principal.model_copy(update={"global_role": GlobalRole.AUDITOR})
    async with service.suites.uow_factory(
        AuthorizationContext.for_principal(auditor, scope=scope)
    ) as work:
        with pytest.raises(DBAPIError, match="review_authorization_invalid"):
            await work.db_session.execute(
                text("SELECT public.opencitadel_e09_command('{}',:signature)"),
                {"signature": "0" * 64},
            )
    with pytest.raises(PermissionError):
        await service.append_score(scope, auditor, candidate.result_id, 0, "auditor", payload)
    assert (await service.list_pending(scope, principal=auditor, status="all")).items


async def test_historical_cursor_keeps_exact_asof_across_later_correction(budget_binding_fixture):
    from app.domain.evaluation.review import HumanScore

    service, scope, principal, _batch, candidate, payload = await review_setup(
        budget_binding_fixture
    )
    payload = payload.model_copy(
        update={
            "scores": (
                HumanScore(dimension="correctness", value=2),
                HumanScore(dimension="completeness", value=2),
            )
        }
    )
    first = await service.append_score(
        scope, principal, candidate.result_id, 0, "history1", payload
    )
    page = await service.history_page(scope, principal, candidate.result_id, limit=1)
    assert page.next_cursor
    assert page.evaluation_revision == 1
    await service.append_score(
        scope,
        principal,
        candidate.result_id,
        1,
        "history2",
        payload.model_copy(
            update={
                "expected_result_revision": first.result_revision,
                "scores": (HumanScore(dimension="correctness", value=4),),
            }
        ),
    )
    second = await service.history_page(
        scope, principal, candidate.result_id, limit=1, cursor=page.next_cursor
    )
    assert second.evaluation_revision == 1
    assert second.items[0].score.value == 2
    assert not second.next_cursor
    with pytest.raises(ValueError, match="invalid_cursor"):
        await service.history_page(
            scope, principal, candidate.result_id, evaluation_revision=2, cursor=page.next_cursor
        )


async def test_cancel_commit_recovery_survives_later_human_head(
    budget_binding_fixture, monkeypatch
):
    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.evaluation.review import HumanReview, HumanScore
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_review_repository import (
        DBEvaluationReviewRepository,
    )

    service, judge, scope, principal, candidate, request = await rescore_setup(
        budget_binding_fixture
    )
    rescore = await service.rescore(
        scope, principal, candidate.result_id, request, "cancel-recovery-source"
    )
    consumer = ReviewCommandConsumer(budget_binding_fixture[-1], judge)
    await consumer.tick()
    rescore = await service.get_command(scope, principal, rescore.id)
    receipt = await service.cancel(
        scope,
        principal,
        candidate.result_id,
        rescore.judge_run_id,
        0,
        candidate.result_revision,
        "cancel-recovery",
    )
    unrelated = await service.cancel(
        scope,
        principal,
        candidate.result_id,
        rescore.judge_run_id,
        0,
        candidate.result_revision,
        "other-cancel-command",
    )
    finish = DBEvaluationReviewRepository.finish

    async def crash(*args, **kwargs):
        raise RuntimeError("worker died after cancel")

    monkeypatch.setattr(DBEvaluationReviewRepository, "finish", crash)
    with pytest.raises(RuntimeError, match="after cancel"):
        await consumer.tick()
    monkeypatch.setattr(DBEvaluationReviewRepository, "finish", finish)
    await service.append_score(
        scope,
        principal,
        candidate.result_id,
        0,
        "human-after-cancel",
        HumanReview(
            rubric_version=request.rubric_version,
            expected_result_revision=candidate.result_revision,
            scores=(HumanScore(dimension="correctness", value=3),),
        ),
    )
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.db_session.execute(
            text(
                "UPDATE evaluation_review_commands SET claim_until=clock_timestamp()-interval '1 second' WHERE kind='cancel'"
            )
        )
        await work.commit()
    await consumer.tick()
    await consumer.tick()
    recovered = await service.get_command(scope, principal, receipt.id)
    assert recovered.status == "cancelled"
    assert recovered.error is None
    other = await service.get_command(scope, principal, unrelated.id)
    assert other.status == "failed"
    assert other.error == "review_revision_conflict"
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_review_cancellations")
            )
            == 1
        )


async def test_model_review_aggregate_uses_each_immutable_case_applicability(
    budget_binding_fixture,
):
    from app.application.evaluation.review_service import ReviewService
    from app.domain.evaluation.batch import schedule_slots
    from app.domain.evaluation.configuration import SuiteDefinition
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.review import HumanReview, HumanScore
    from app.domain.evaluation.rubric import RequiredCondition, RubricDefinition
    from app.domain.models.authorization import AuthorizationContext

    suites, scope, principal, old_suite, *tail = budget_binding_fixture
    draft = await suites.datasets.create_draft(
        scope, principal, request_id=str(uuid4()), expected_revision=0, name="Mixed review"
    )
    cases = [
        CaseRevision(case_key="required", input="one", applicable_dimensions=("correctness",)),
        CaseRevision(case_key="inapplicable", input="two", applicable_dimensions=("completeness",)),
    ]
    for revision, case in enumerate(cases, 1):
        draft = await suites.datasets.update_case(
            scope,
            principal,
            dataset_id=draft.id,
            request_id=str(uuid4()),
            expected_revision=revision,
            case=case,
        )
    dataset = await suites.datasets.publish(
        scope, principal, dataset_id=draft.id, expected_revision=3, request_id=str(uuid4())
    )
    first = schedule_slots(
        [c.id for c in dataset.cases],
        old_suite.config_versions,
        old_suite.settings.repeat,
        old_suite.settings.seed,
    )[0]
    required_case = next(c for c in dataset.cases if c.id == first.case_revision_id)
    dimension = required_case.applicable_dimensions[0]
    old_rubric = await suites.get_version(scope, principal, "rubric", old_suite.rubric_version)
    definition = RubricDefinition(
        **{k: getattr(old_rubric, k) for k in RubricDefinition.model_fields}
    ).model_copy(
        update={
            "required_conditions": (
                RequiredCondition(dimension_id=dimension, minimum=3, source="human"),
            )
        }
    )
    rubric_draft = await suites.create(
        scope,
        principal,
        kind="rubric",
        name="Mixed",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    rubric = await suites.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=rubric_draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    definition = SuiteDefinition(
        **{k: getattr(old_suite, k) for k in SuiteDefinition.model_fields}
    ).model_copy(update={"dataset_version": dataset.id, "rubric_version": rubric.id})
    suite_draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Mixed",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    suite = await suites.publish(
        scope,
        principal,
        kind="suite",
        entity_id=suite_draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    fixture = (suites, scope, principal, suite, *tail)
    _, _, batch, candidate = await completed(fixture)
    assert candidate.case_revision_id == required_case.id
    service = ReviewService(suites)
    receipt = await service.append_score(
        scope,
        principal,
        candidate.result_id,
        0,
        "mixed-human",
        HumanReview(
            rubric_version=rubric.id,
            expected_result_revision=candidate.result_revision,
            scores=(HumanScore(dimension=dimension, value=3),),
        ),
    )
    async with tail[-1](AuthorizationContext.system("execution-kernel")) as work:
        await work.db_session.execute(
            text(
                "UPDATE evaluation_batch_results SET execution_status='failed' WHERE batch_id=:batch AND id<>:id"
            ),
            {"batch": batch.id, "id": candidate.result_id},
        )
        await work.evaluation_review.model_rubric(
            scope,
            candidate,
            rubric.id,
            rubric.model_dump(mode="json"),
            (dimension,),
            receipt.evaluation_revision + 1,
        )
        saved = await work.evaluation_batch.get(scope, batch.id)
        assert saved["review_status"] == "complete"
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM evaluation_review_states")) == 1
        )
        await work.commit()
    assert not (await service.list_pending(scope, principal=principal)).items


async def test_legacy_batch_model_settlement_backfills_immutable_requirements(
    budget_binding_fixture, monkeypatch
):
    import json
    from types import SimpleNamespace

    from app.application.evaluation.review_consumer import ReviewCommandConsumer
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_review_repository import (
        DBEvaluationReviewRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_judge_repository import finish_judge

    async def legacy_materialization(*args, **kwargs):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(DBEvaluationReviewRepository, "requirements", legacy_materialization)
        service, judge, scope, principal, candidate, request = await rescore_setup(
            budget_binding_fixture
        )
    command = await service.rescore(scope, principal, candidate.result_id, request, "legacy-model")
    consumer = ReviewCommandConsumer(budget_binding_fixture[-1], judge)
    await consumer.tick()
    command = await service.get_command(scope, principal, command.id)
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_review.requirements(scope, candidate.batch_id) is None
        with pytest.raises(ValueError, match="review_requirements_unavailable"):
            await work.evaluation_review.model_rubric(
                scope, candidate, request.rubric_version, {}, (), 1
            )
        intent = await work.evaluation_judge.get(scope, command.judge_run_id)
    await finish_judge(
        budget_binding_fixture,
        SimpleNamespace(execution_policy=judge.execution_policy),
        intent,
        json.dumps(
            {
                "status": "complete",
                "dimensions": [
                    {"name": d["id"], "score": 4, "reason": "match", "evidence": []}
                    for d in intent["materials"]["rubric"]
                ],
                "unavailable_reason": None,
            }
        ),
    )
    assert await judge.reconcile_batch(scope, candidate.batch_id) == 1
    async with budget_binding_fixture[-1](AuthorizationContext.system("execution-kernel")) as work:
        fixed = await work.evaluation_review.requirements(scope, candidate.batch_id)
        assert fixed == {str(candidate.case_revision_id): False}
        assert (
            await work.evaluation_review.requirements(
                scope, candidate.batch_id, {str(candidate.case_revision_id): True}
            )
            == fixed
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            await work.db_session.execute(
                text("UPDATE evaluation_review_requirements SET requirements='{}'")
            )


@pytest.mark.parametrize("phase", ["pending", "unaccepted", "running", "terminal"])
@pytest.mark.parametrize("revocation", ["principal", "dataset"])
async def test_legacy_unavailable_requirements_do_not_block_judge_cleanup(
    budget_binding_fixture, monkeypatch, phase, revocation
):
    import json
    from types import SimpleNamespace

    from app.domain.evaluation.configuration import SuiteDefinition
    from app.domain.evaluation.dataset import CaseRevision
    from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_evaluation_review_repository import (
        DBEvaluationReviewRepository,
    )
    from tests.app.execution_test_support import execution_admin_session
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        actual_handler,
    )
    from tests.app.infrastructure.repositories.test_evaluation_judge_repository import finish_judge

    fixture = budget_binding_fixture
    suites, scope, principal, suite, *tail = fixture
    if revocation == "dataset":
        file_id = str(uuid4())
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "INSERT INTO files(id,owner_user_id,key,content_digest,object_identity) VALUES (:id,:owner,'legacy-source',:digest,:object)"
                ),
                {"id": file_id, "owner": principal.user_id, "digest": "a" * 64, "object": uuid4()},
            )
            await db.commit()
        draft = await suites.datasets.create_draft(
            scope, principal, request_id=str(uuid4()), expected_revision=0, name="Legacy source"
        )
        draft = await suites.datasets.update_case(
            scope,
            principal,
            dataset_id=draft.id,
            request_id=str(uuid4()),
            expected_revision=1,
            case=CaseRevision(case_key="source", input="Question", attachments=(file_id,)),
        )
        dataset = await suites.datasets.publish(
            scope, principal, dataset_id=draft.id, expected_revision=2, request_id=str(uuid4())
        )
        definition = SuiteDefinition(
            **{key: getattr(suite, key) for key in SuiteDefinition.model_fields}
        ).model_copy(update={"dataset_version": dataset.id})
        draft = await suites.create(
            scope,
            principal,
            kind="suite",
            name="Legacy source",
            definition=definition.model_dump(mode="json"),
            request_id=str(uuid4()),
        )
        suite = await suites.publish(
            scope,
            principal,
            kind="suite",
            entity_id=draft.id,
            expected_revision=1,
            request_id=str(uuid4()),
        )
        fixture = (suites, scope, principal, suite, *tail)

    async def legacy_materialization(*args, **kwargs):
        return None

    with monkeypatch.context() as patch:
        patch.setattr(DBEvaluationReviewRepository, "requirements", legacy_materialization)
        _, judge, scope, principal, candidate, request = await rescore_setup(fixture)
    with monkeypatch.context() as patch:
        if phase == "pending":

            async def capacity(*args, **kwargs):
                raise ExecutionCapacityUnavailable("test-held-capacity")

            patch.setattr(judge, "_admit", capacity)
        for index in range(2):
            if phase == "pending":
                with pytest.raises(ExecutionCapacityUnavailable):
                    await judge.rescore(
                        scope, candidate, request, f"legacy-cleanup-{index}", principal=principal
                    )
            else:
                await judge.rescore(
                    scope, candidate, request, f"legacy-cleanup-{index}", principal=principal
                )
    auth = AuthorizationContext.system("execution-kernel")
    async with fixture[-1](auth) as work:
        assert await work.evaluation_review.requirements(scope, candidate.batch_id) is None
        intents = await work.evaluation_judge.active(scope, candidate.batch_id)
        assert len(intents) == 2
    handler = actual_handler(fixture, judge.execution_policy)
    if phase == "running":
        for intent in intents:
            envelope = CommandEnvelope.model_validate(intent["envelope"])
            assert (await handler.handle(envelope)).status == "accepted"
            assert (
                await handler.handle(
                    envelope.model_copy(
                        update={
                            "command_id": uuid4(),
                            "command_type": "StartRun",
                            "expected_stream_version": None,
                            "payload": {},
                        }
                    )
                )
            ).status == "accepted"
    if phase == "terminal":
        for intent in intents:
            await finish_judge(
                fixture,
                SimpleNamespace(execution_policy=judge.execution_policy),
                intent,
                json.dumps(
                    {
                        "status": "complete",
                        "dimensions": [
                            {"name": d["id"], "score": 4, "reason": "match", "evidence": []}
                            for d in intent["materials"]["rubric"]
                        ],
                        "unavailable_reason": None,
                    }
                ),
            )
    async with execution_admin_session() as db:
        if revocation == "principal":
            await db.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
        else:
            await db.execute(
                text("UPDATE files SET content_available=false WHERE id=:id"), {"id": file_id}
            )
        await db.commit()
    assert await judge.reconcile_batch(scope, candidate.batch_id) == 0
    assert await judge.reconcile_batch(scope, candidate.batch_id) == 0
    if phase == "running":
        async with fixture[-1](auth) as work:
            for intent in intents:
                saved = await work.evaluation_judge.get(scope, intent["run_id"])
                assert saved["status"] == "submitted"
                assert saved["cancel_envelope"] is not None
                assert (
                    await work.evaluation_budget_control.namespace(scope, intent["namespace_id"])
                ).state == "open"
        for intent in intents:
            async with fixture[-1](auth) as work:
                saved = await work.evaluation_judge.get(scope, intent["run_id"])
            assert (
                await handler.handle(CommandEnvelope.model_validate(saved["cancel_envelope"]))
            ).status == "accepted"
        await PostgresFormalProjector(
            session_factory=handler._session_factory, authorization=auth
        ).run_once(scope, limit=100)
        assert await judge.reconcile_batch(scope, candidate.batch_id) == 0
    async with fixture[-1](auth) as work:
        assert await work.evaluation_review.requirements(scope, candidate.batch_id) is None
        assert await work.evaluation_score.revision(scope, candidate.batch_id) == 0
        for intent in intents:
            assert (await work.evaluation_judge.get(scope, intent["run_id"]))["status"] == "stopped"
            if phase != "pending":
                assert (
                    await work.evaluation_budget_control.namespace(scope, intent["namespace_id"])
                ).state == "closed"
            if phase == "unaccepted":
                envelope = CommandEnvelope.model_validate(intent["envelope"])
                receipt = await work.evaluation_batch.receipt(
                    scope, {"run_id": intent["run_id"], "command_id": envelope.command_id}
                )
                assert receipt["status"] in {"rejected", "dead_lettered"}
