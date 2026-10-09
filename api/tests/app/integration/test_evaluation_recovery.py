# ruff: noqa: F401,F811
"""Strict owned-database lifecycle discovery and fencing, never shared schema."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_environment_repository import (
    environment_kernel,
)
from tests.app.infrastructure.repositories.test_evaluation_physical_dispatch import ready_dispatch

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_first_batch_is_discovered_before_any_run_and_expired_owner_cannot_renew(
    budget_binding_fixture,
):
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.runtime_inventory import EvaluationRuntimeInventory

    _, scope, principal, suite, _, _, factory = budget_binding_fixture
    batch_id = uuid4()
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        await work.evaluation_batch.submit(
            scope, principal, "start", "e12-first", {"suite_version": str(suite.id)}, batch_id
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_stream_owners")) == 0
        )
        await work.commit()
    inventory = EvaluationRuntimeInventory(factory)
    assert await inventory.discover() == [(scope, batch_id)]
    async with factory(auth) as work:
        claim = await work.evaluation_batch.claim(datetime.now(UTC), lease_seconds=1)
        await work.commit()
    async with factory(auth) as work:
        replacement = await work.evaluation_batch.claim(datetime.now(UTC) + timedelta(seconds=2))
        assert replacement["generation"] > claim["generation"]
        with pytest.raises(ValueError, match="claim_lost"):
            await work.evaluation_batch.renew(claim)
        await work.evaluation_batch.renew(replacement)
        await work.evaluation_batch.release(claim)
        assert (await work.evaluation_batch.get(scope, batch_id))["claim_until"] is not None
        await work.evaluation_batch.release(replacement)
        await work.commit()
    async with factory(auth) as work:
        assert (await work.evaluation_batch.get(scope, batch_id))["claim_until"] is None


async def test_archive_is_idempotent_keeps_history_and_rejects_new_work(budget_binding_fixture):
    from app.application.evaluation.archive_service import ArchiveService
    from app.domain.evaluation.errors import DatasetConflict
    from app.domain.models.authorization import AuthorizationContext

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    service = ArchiveService(suites.uow_factory)
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        row = await work.db_session.execute(
            text("SELECT id,revision FROM evaluation_datasets WHERE scope_key=:scope"),
            {"scope": "user:" + principal.user_id},
        )
        identity, revision = row.one()
    receipt = await service.archive(
        scope,
        principal,
        kind="dataset",
        identity=identity,
        expected_revision=revision,
        request_id="e12-archive",
    )
    assert receipt["state"] == "archived"
    assert receipt == await service.archive(
        scope,
        principal,
        kind="dataset",
        identity=identity,
        expected_revision=revision,
        request_id="e12-archive",
    )
    with pytest.raises(DatasetConflict):
        await service.archive(
            scope,
            principal,
            kind="dataset",
            identity=identity,
            expected_revision=revision + 1,
            request_id="e12-archive",
        )
    assert (
        await suites.datasets.get_version(scope, principal, suite.dataset_version)
    ).id == suite.dataset_version
    assert await suites.datasets.list_drafts(scope, principal) == []
    async with factory(AuthorizationContext.for_principal(principal, scope=scope)) as work:
        with pytest.raises(ValueError, match="archived"):
            await work.evaluation_archive.require_active(scope, "dataset", identity)


@pytest.mark.parametrize("boundary", ["admission_commit", "inbox_commit"])
async def test_runtime_restart_reuses_prepared_admission_after_crash(
    budget_binding_fixture, monkeypatch, boundary
):
    import asyncio

    from app.application.evaluation.runtime import EvaluationRuntime
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.repositories.db_evaluation_batch_repository import (
        DBEvaluationBatchRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        scheduled_batch,
    )

    _, scope, _, _, _, _, factory = budget_binding_fixture
    _, scheduler, batch = await scheduled_batch(budget_binding_fixture)
    runtime = EvaluationRuntime(
        scheduler=scheduler, rules=None, judge=None, reviews=None, discover=None, cleanup=()
    )
    original_admit = scheduler.admission.admit
    original_submitted = DBEvaluationBatchRepository.submitted
    if boundary == "admission_commit":

        async def lost_admission(**kwargs):
            await original_admit(**kwargs)
            raise ConnectionError("crash after committed admission")

        scheduler.admission.admit = lost_admission
    else:

        async def lost_inbox(*args, **kwargs):
            raise ConnectionError("crash before committed inbox")

        monkeypatch.setattr(DBEvaluationBatchRepository, "submitted", lost_inbox)
    with pytest.raises(ConnectionError, match="crash"):
        await runtime.run(runtime.schedule, stop_event=asyncio.Event())
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (await work.evaluation_batch.get(scope, batch.id))["claim_until"] is None
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        frozen = row["prepared_envelope"]
        assert frozen is not None
    scheduler.admission.admit = original_admit
    monkeypatch.setattr(DBEvaluationBatchRepository, "submitted", original_submitted)
    restarted = EvaluationRuntime(
        scheduler=scheduler, rules=None, judge=None, reviews=None, discover=None, cleanup=()
    )
    await restarted.schedule()
    await restarted.schedule()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        assert row["envelope"] == frozen
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_command_inbox WHERE command_type='CreateRun'")
            )
            == 1
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_configurations")) == 1
        )
        assert (await work.evaluation_batch.get(scope, batch.id))["claim_until"] is None


async def test_supervised_scoring_crash_before_commit_keeps_one_immutable_revision(
    budget_binding_fixture, monkeypatch
):
    import asyncio

    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.application.evaluation.runtime import EvaluationRuntime
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.repositories.db_evaluation_score_repository import (
        DBEvaluationScoreRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_score_repository import (
        completed,
        with_rules,
    )

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "text_exact", "expected": "answer"}]
    )
    suites, scope, _, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output="answer")
    scoring = RuleScoringService(
        factory, suites, RuleEvidenceReader(factory, content_factory=lambda auth: None)
    )
    append = DBEvaluationScoreRepository.append

    async def crash(*args, **kwargs):
        await append(*args, **kwargs)
        raise ConnectionError("score computed but transaction uncommitted")

    monkeypatch.setattr(DBEvaluationScoreRepository, "append", crash)
    with pytest.raises(ConnectionError, match="uncommitted"):
        await EvaluationRuntime.run(
            lambda: scoring.score(scope, candidate), stop_event=asyncio.Event()
        )
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_score_sets WHERE batch_id=:id"),
                {"id": batch.id},
            )
            == 0
        )
    monkeypatch.setattr(DBEvaluationScoreRepository, "append", append)
    assert await scoring.score(scope, candidate) == 1
    assert await scoring.score(scope, candidate) == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert len(history) == 1
        assert history[0].score.value is True


async def test_supervised_scoring_restart_after_committed_score_before_ack(budget_binding_fixture):
    import asyncio

    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.application.evaluation.runtime import EvaluationRuntime
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from tests.app.infrastructure.repositories.test_evaluation_score_repository import (
        completed,
        with_rules,
    )

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "text_exact", "expected": "answer"}]
    )
    suites, scope, _, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output="answer")
    scoring = RuleScoringService(
        factory, suites, RuleEvidenceReader(factory, content_factory=lambda auth: None)
    )

    async def lost_ack():
        assert await scoring.score(scope, candidate) == 1  # actual service commits before returning
        raise ConnectionError("process died after score commit before acknowledgement")

    with pytest.raises(ConnectionError, match="after score commit"):
        await EvaluationRuntime.run(lost_ack, stop_event=asyncio.Event())
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        original = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert len(original) == 1
    restarted = RuleScoringService(
        factory, suites, RuleEvidenceReader(factory, content_factory=lambda auth: None)
    )
    stop = asyncio.Event()

    async def recovered():
        assert await restarted.score(scope, candidate) == 1
        stop.set()

    await EvaluationRuntime.run(recovered, stop_event=stop)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.evaluation_score.history(scope, batch.id, evaluation_revision=1) == original
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_score_sets WHERE batch_id=:id"),
                {"id": batch.id},
            )
            == 1
        )


async def test_owning_budget_recovery_retains_unknown_until_exact_late_settlement(
    datasets, environment_kernel
):
    # Exact existing real repository boundary, included in the declared E12 target.
    from tests.app.infrastructure.repositories.test_evaluation_budget_repository import (
        test_unknown_preserves_tokens_and_slots_late_usage_is_exactly_once as verify,
    )

    await verify(datasets, environment_kernel)


async def test_owning_physical_recovery_after_accepted_cancel_and_requester_revocation(
    ready_dispatch,
):
    # Uses the real orchestrator, F07 dispatch, persisted CancelRun and independent late fact;
    # cancellation stays terminal and settlement/budget release remain exactly-once.
    from tests.app.infrastructure.repositories.test_evaluation_physical_dispatch import (
        test_unknown_late_usage_settles_f07_and_budget_once_after_cancel_and_revocation as verify,
    )

    await verify(ready_dispatch)


async def test_supervised_cleanup_failure_retains_quarantine_and_exact_late_ack(
    datasets, environment_kernel
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.evaluation.environment_service import EnvironmentService, EnvironmentWorker
    from app.application.evaluation.runtime import EvaluationRuntime
    from app.domain.evaluation.environment import CaseSlot, EnvironmentLease
    from app.domain.evaluation.errors import EnvironmentTransportUnknown
    from tests.app.infrastructure.repositories.test_evaluation_environment_repository import version

    _, scope, principal, _, _ = datasets
    env = version()
    lease = EnvironmentLease(
        id=uuid4(),
        environment_version=env.id,
        case_slot=CaseSlot(
            workspace="user:" + scope.user_id,
            batch_id=uuid4(),
            case_id=uuid4(),
            config_version=uuid4(),
            repeat=1,
        ),
        generation=1,
        revision=1,
        state="allocated",
        requester=principal.model_dump(mode="json"),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    async with environment_kernel() as work:
        await work.evaluation_environment.register(scope, "environment", env)
        await work.evaluation_environment.allocate(scope, lease, (), concurrency=2)
        lease = await work.evaluation_environment.begin(scope, lease, "cleaning", "cleanup")
        pending = await work.evaluation_environment.pending()
        operation_id = pending[0]["id"]
        await work.commit()
    physical = SimpleNamespace(
        cleanup=AsyncMock(side_effect=EnvironmentTransportUnknown("reply lost after cleanup send"))
    )
    worker = EnvironmentWorker(environment_kernel, SimpleNamespace(resolve=lambda *args: physical))
    runtime = EvaluationRuntime(
        scheduler=None,
        rules=None,
        judge=None,
        reviews=None,
        discover=None,
        cleanup=[lambda: worker.process(scope, operation_id)],
    )
    await runtime.clean()
    async with environment_kernel() as work:
        repo = work.evaluation_environment
        current = await repo.lease(scope, lease.id)
        assert current.state == "quarantine"
        assert current.id == lease.id
        assert current.generation == lease.generation
        assert await repo.unresolved_operations(scope, lease.id)
        with pytest.raises(ValueError, match="unresolved_operation"):
            await EnvironmentService(environment_kernel, SimpleNamespace()).cleanup_in_uow(
                work, scope, lease.id, repair=True, principal=principal
            )
    # Restart does not retry an ambiguous cleanup, or clear quarantine from a receipt.
    await runtime.clean()
    physical.cleanup.assert_awaited_once()
    async with environment_kernel() as work:
        from app.domain.evaluation.environment import EnvironmentOperation

        row = (
            (
                await work.db_session.execute(
                    text("SELECT * FROM evaluation_environment_operations WHERE id=:id"),
                    {"id": operation_id},
                )
            )
            .mappings()
            .one()
        )
        operation = EnvironmentOperation.model_validate(
            {key: row[key] for key in EnvironmentOperation.model_fields}
        )
        assert not await work.evaluation_environment.complete(scope, operation, {"resources": []})
        assert (await work.evaluation_environment.lease(scope, lease.id)).state == "quarantine"
        assert not await work.evaluation_environment.unresolved_operations(scope, lease.id)
        await work.commit()
    # Recovery requires a fresh explicit administrator repair and native verification.
    from app.domain.models.scope import Principal
    from app.domain.models.user import GlobalRole
    from tests.app.execution_test_support import execution_admin_session

    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE users SET global_role='admin' WHERE id=:id"), {"id": principal.user_id}
        )
        await db.commit()
    administrator = Principal(user_id=principal.user_id, global_role=GlobalRole.ADMIN)
    async with environment_kernel() as work:
        repaired = await EnvironmentService(environment_kernel, SimpleNamespace()).cleanup_in_uow(
            work, scope, lease.id, repair=True, principal=administrator
        )
        assert repaired.state == "cleaning"
        await work.commit()
    physical.cleanup = AsyncMock(return_value={"resources": []})
    physical.verify = AsyncMock(return_value={"verified": True, "resources": []})

    async def recover_pending():
        async with environment_kernel() as work:
            pending = await work.evaluation_environment.pending()
        for item in pending:
            await worker.process(scope, item["id"])

    runtime.cleanup = (recover_pending,)
    await runtime.clean()
    await runtime.clean()
    async with environment_kernel() as work:
        final = await work.evaluation_environment.lease(scope, lease.id)
        assert final.state == "verified_clean"
        assert final.generation == lease.generation
        assert not await work.evaluation_environment.unresolved_operations(scope, lease.id)
    physical.cleanup.assert_awaited_once()
    physical.verify.assert_awaited_once()
