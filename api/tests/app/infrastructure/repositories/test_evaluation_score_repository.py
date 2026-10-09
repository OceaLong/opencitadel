# ruff: noqa: F401,F811
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
    actual_handler,
    scheduled_batch,
)
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def with_rules(fixture, rules):
    from app.domain.evaluation.configuration import SuiteDefinition
    from app.domain.evaluation.dataset import CaseRevision

    suites, scope, principal, suite, *tail = fixture
    datasets = suites.datasets
    draft = await datasets.create_draft(
        scope, principal, request_id=str(uuid4()), expected_revision=0, name="Rules"
    )
    draft = await datasets.update_case(
        scope,
        principal,
        dataset_id=draft.id,
        request_id=str(uuid4()),
        expected_revision=1,
        case=CaseRevision(case_key="rules", input="Question", rules=tuple(rules)),
    )
    version = await datasets.publish(
        scope, principal, dataset_id=draft.id, expected_revision=2, request_id=str(uuid4())
    )
    definition = SuiteDefinition(
        **{k: getattr(suite, k) for k in SuiteDefinition.model_fields}
    ).model_copy(update={"dataset_version": version.id})
    draft = await suites.create(
        scope,
        principal,
        kind="suite",
        name="Rules",
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


async def completed(fixture, *, final_output=None, final_ref=True, before_model=None):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    _, scope, _, _, _, _, factory = fixture
    service, scheduler, batch = await scheduled_batch(fixture)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
    handler = actual_handler(fixture, scheduler.execution_policy)
    create = CommandEnvelope.model_validate(row["envelope"])
    assert (await handler.handle(create)).status == "accepted"

    async def command(kind, payload, version=1, command_id=None):
        assert (
            await handler.handle(
                create.model_copy(
                    update={
                        "command_id": command_id or uuid4(),
                        "command_type": kind,
                        "command_schema_version": version,
                        "expected_stream_version": None,
                        "payload": payload,
                    }
                )
            )
        ).status == "accepted"

    await command("StartRun", {})
    if before_model:
        await before_model(row, handler, command)
    result_ref = None
    if final_output is not None:
        from datetime import timedelta

        from app.domain.models.artifact_provenance import ArtifactProducer
        from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
        from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter

        activity = uuid4()
        await command(
            "RequestActivity",
            {
                "activity_id": str(activity),
                "activity_type": "model.call",
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "input_ref": "e07/input",
                "input_digest": "a" * 64,
            },
            2,
        )
        auth = AuthorizationContext.system("execution-kernel")
        store = PostgresActivityStore(session_factory=handler._session_factory, authorization=auth)
        claim = (
            await store.claim(
                now=datetime.now(UTC), limit=1, worker_id="e07", claim_ttl=timedelta(minutes=5)
            )
        )[0]
        assert await store.mark_call_started(claim, now=datetime.now(UTC))
        await command(
            "MarkActivityCallStarted",
            {
                "activity_id": str(activity),
                "generation": 0,
                "claim_generation": claim.claim_generation,
            },
            2,
        )
        command_id = uuid4()
        writer = ExecutionContentWriter(
            session_factory=handler._session_factory, authorization=auth, objects=None
        )
        await writer.record(
            ArtifactProducer(
                scope=scope,
                run_id=row["run_id"],
                activity_id=activity,
                generation=0,
                claim_generation=claim.claim_generation,
            ),
            command_id=command_id,
            phase="output",
            value={"kind": "model", "message": {"role": "assistant", "content": final_output}},
        )
        result_ref = "e07/exact-final-ref"
        await command(
            "CompleteActivity",
            {
                "activity_id": str(activity),
                "generation": 0,
                "claim_generation": claim.claim_generation,
                "result_ref": result_ref,
                "result_summary": "truncated preview",
            },
            2,
            command_id,
        )
    await command("CompleteRun", {"result_ref": result_ref} if final_ref and result_ref else {})
    await PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    ).run_once(scope, limit=100)
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        candidate = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]
    return service, scheduler, batch, candidate


async def test_atomic_immutable_source_revisions_and_pending_model(budget_binding_fixture):
    from app.domain.evaluation.scoring import ScoreValue
    from app.domain.models.authorization import AuthorizationContext

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, _, batch, candidate = await completed(budget_binding_fixture)
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    score = ScoreValue(dimension="rule:0", rubric_revision=rubric.id, status="valid", value=False)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        revision = await work.evaluation_score.append(
            scope,
            principal,
            candidate,
            source="rule",
            scores=(score,),
            expected_evaluation_revision=0,
            request_id="rules",
            required_dimensions=("rule:0",),
            applicable_dimensions=tuple(d.id for d in rubric.dimensions),
        )
        assert revision == 1
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.evaluation_score.append(
                scope,
                principal,
                candidate,
                source="rule",
                scores=(score,),
                expected_evaluation_revision=0,
                request_id="rules",
                required_dimensions=("rule:0",),
                applicable_dimensions=tuple(d.id for d in rubric.dimensions),
            )
            == 1
        )
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert len(history) == 1
        assert history[0].score.value is False
        assert (await work.evaluation_batch.results(scope, batch.id))[0][
            "scoring_status"
        ] == "pending"
        assert await work.db_session.scalar(text("SELECT count(*) FROM evaluation_score_sets")) == 1
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_batch_events WHERE kind='scores_appended'")
            )
            == 1
        )
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(DBAPIError, match="permission denied"):
            await work.db_session.execute(text("UPDATE evaluation_scores SET reason='changed'"))


async def test_score_consumer_missing_terminal_ref_settles_rule_only(budget_binding_fixture):
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, _principal, _, _, _, factory = budget_binding_fixture
    _, _, batch, candidate = await completed(budget_binding_fixture)
    reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
    service = RuleScoringService(factory, suites, reader)
    assert await service.score(scope, candidate) == 1
    assert await service.score(scope, candidate) == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (
            await work.evaluation_score.settled(scope, candidate.result_id, "rule")
            == "not_required"
        )
        assert await work.evaluation_score.settled(scope, candidate.result_id, "model") is None
        updated = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]
    assert await service.score(scope, updated) == 1


@pytest.mark.parametrize("final_ref", [True, False])
async def test_reader_uses_exact_final_ref_full_f06_body(budget_binding_fixture, final_ref):
    from app.domain.evaluation.rule_engine import MISSING
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    _, scope, principal, _, _, _, factory = budget_binding_fixture
    output = "Exact 🙂 final\n" * 10000
    _, _, _, candidate = await completed(
        budget_binding_fixture, final_output=output, final_ref=final_ref
    )
    reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
    evidence = await reader.read(scope, principal, candidate, resources=())
    if final_ref:
        assert evidence.subject == output
        assert evidence.content_id is not None
    else:
        assert evidence.subject is MISSING  # Never guess the last available model output.


@pytest.mark.parametrize(
    "during_current",
    [
        False,
        True,
        "before_schedule",
        "before_create",
        "before_rule_append",
        "failed_projection",
        "rejected_receipt",
        "failed_projection_rule",
        "rejected_receipt_rule",
    ],
)
async def test_reader_keeps_terminal_output_after_usage_event(
    budget_binding_fixture, monkeypatch, during_current
):
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.repositories.db_execution_usage_repository import (
        DBExecutionUsageRepository,
    )

    automatic = isinstance(during_current, str)
    rule_lane = during_current == "before_rule_append" or (
        automatic and during_current.endswith("_rule")
    )
    guarded = automatic and during_current.startswith(("failed_", "rejected_"))
    if rule_lane:
        budget_binding_fixture = await with_rules(
            budget_binding_fixture, [{"kind": "text_exact", "expected": "answer"}]
        )
    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, scheduler, batch, original = await completed(budget_binding_fixture, final_output="answer")
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = (
            None
            if automatic
            else await work.evaluation_judge.create(
                scope,
                principal,
                original,
                rubric_id=rubric.id,
                config_id=rubric.judge_config_version,
                request_id="judge-before-late-usage",
                materials={},
                namespace_id=batch.id,
            )
        )
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        activity = await work.db_session.scalar(
            text(
                """SELECT activity_id FROM execution_activity_projection
                WHERE run_id=:run AND activity_type='model.call'"""
            ),
            {"run": original.run_id},
        )
        config = await DBExecutionUsageRepository(work.db_session).snapshot(
            scope, original.run_id, {"test": "late usage publication"}, "evaluation_subject"
        )
        call_identity = str(uuid4())
        await work.db_session.execute(
            text("""INSERT INTO execution_model_dispatches
                (call_identity,run_id,activity_id,generation,claim_generation,
                 attempt_id,logical_group,ordinal,configuration_id,
                 request_snapshot,owner_user_id,team_id,created_by)
                VALUES (:identity,:run,:activity,0,1,:attempt,'invoke:0',1,
                        :config,'{}'::jsonb,:owner,:team,'test')"""),
            {
                "identity": call_identity,
                "run": original.run_id,
                "activity": activity,
                "attempt": str(uuid4()),
                "config": config,
                "owner": scope.user_id,
                "team": scope.team_id,
            },
        )
        await work.commit()
    command = CommandEnvelope.model_validate(row["envelope"])
    handler = actual_handler(budget_binding_fixture, scheduler.execution_policy)
    recorded = await handler.handle(
        command.model_copy(
            update={
                "command_id": uuid4(),
                "command_type": "RecordModelUsage",
                "command_schema_version": 1,
                "expected_stream_version": None,
                "payload": {"call_identity": call_identity, "phase": "dispatch"},
            }
        )
    )
    assert recorded.status == "accepted"
    await PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    ).run_once(scope, limit=100)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        await DBExecutionUsageRepository(work.db_session).record(
            scope,
            call_identity,
            {
                "call_identity": call_identity,
                "configuration_id": config,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "model_revision": "test-model",
                "price_revision": "test-price",
                "cost_usd": None,
                "version_unpinned": False,
            },
        )
        await work.commit()
    settled = await handler.handle(
        command.model_copy(
            update={
                "command_id": uuid4(),
                "command_type": "RecordModelUsage",
                "command_schema_version": 1,
                "expected_stream_version": None,
                "payload": {"call_identity": call_identity, "phase": "settlement"},
            }
        )
    )
    assert settled.status == "accepted"
    projector = PostgresFormalProjector(
        session_factory=handler._session_factory,
        authorization=AuthorizationContext.system("execution-kernel"),
    )
    if during_current is True:
        import asyncio

        from sqlalchemy.ext.asyncio import create_async_engine

        from core.config import load_deployment_settings
        from tests.app.execution_test_support import (
            authenticated_session_factory,
            execution_admin_session,
        )

        writer_engine = create_async_engine(handler._session_factory.kw["bind"].url)
        projector = PostgresFormalProjector(
            session_factory=authenticated_session_factory(
                writer_engine,
                signing_secret=load_deployment_settings().database_authorization_signing_secret,
            ),
            authorization=AuthorizationContext.system("execution-kernel"),
        )
        entered = asyncio.Event()
        writer_pid = None
        writer = None
        original_flush = projector._flush_run

        async def publish(session, tracker):
            nonlocal writer_pid
            writer_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
            await original_flush(session, tracker)

        monkeypatch.setattr(projector, "_flush_run", publish)
        try:
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                original_projection = work.evaluation_batch.projection
                first_read = True
                role = (
                    await work.db_session.execute(
                        text(
                            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
                        )
                    )
                ).one()
                assert role == (False, False)
                with pytest.raises(ValueError, match="scoring_candidate_stale"):
                    await work.evaluation_judge.current(
                        scope.model_copy(update={"user_id": "other-current-user"}), intent
                    )
                reader_pid = await work.db_session.scalar(text("SELECT pg_backend_pid()"))
                before_revision = await work.db_session.scalar(
                    text("SELECT stream_version FROM execution_run_projection WHERE run_id=:run"),
                    {"run": original.run_id},
                )

                async def between_reads(current_scope, current_row):
                    nonlocal first_read, writer
                    projection = await original_projection(current_scope, current_row)
                    if first_read:
                        first_read = False
                        writer = asyncio.create_task(projector.run_once(scope, limit=100))
                        await asyncio.wait_for(entered.wait(), timeout=10)
                        # The writer either commits (the original race) or is blocked
                        # by this UoW's shared projection lock. Observe PostgreSQL,
                        # rather than delaying either operation with a fixed sleep.
                        async with execution_admin_session() as observer:
                            async with asyncio.timeout(10):
                                while not writer.done():
                                    blockers = await observer.scalar(
                                        text("SELECT pg_blocking_pids(:pid)"),
                                        {"pid": writer_pid},
                                    )
                                    if reader_pid in blockers:
                                        break
                                    await asyncio.sleep(0.01)
                    return projection

                monkeypatch.setattr(work.evaluation_batch, "projection", between_reads)
                stable, requester = await work.evaluation_judge.current(scope, intent)
                assert stable == original.model_copy(update={"run_revision": before_revision})
                assert requester == principal
                assert writer is not None
                assert not writer.done()
                await work.commit()
            await asyncio.wait_for(writer, timeout=10)
        finally:
            if writer is not None and not writer.done():
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            await writer_engine.dispose()
    elif automatic:
        from app.application.evaluation.judge_service import JudgeService
        from app.application.evaluation.rule_scoring_service import RuleScoringService

        reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
        service = JudgeService(
            factory,
            suites,
            reader,
            scheduler.admission,
            execution_policy=scheduler.execution_policy,
        )
        published = False

        async def publish_usage():
            nonlocal published
            if not published:
                published = True
                await projector.run_once(scope, limit=100)
                if guarded:
                    async with factory(AuthorizationContext.system("execution-kernel")) as work:
                        if during_current.startswith("failed_"):
                            await work.db_session.execute(
                                text(
                                    "UPDATE execution_run_projection SET status='failed' WHERE run_id=:run"
                                ),
                                {"run": original.run_id},
                            )
                        else:
                            await work.db_session.execute(
                                text(
                                    "UPDATE execution_command_inbox SET status='rejected',rejection_code='TEST_REJECTION' WHERE command_id=:command"
                                ),
                                {"command": row["command_id"]},
                            )
                        await work.commit()

        if during_current == "before_schedule" or (guarded and not rule_lane):
            schedule = service.schedule

            async def before_schedule(*args, **kwargs):
                await publish_usage()
                return await schedule(*args, **kwargs)

            monkeypatch.setattr(service, "schedule", before_schedule)
        elif during_current == "before_create":
            read = reader.read

            async def before_create(*args, **kwargs):
                evidence = await read(*args, **kwargs)
                await publish_usage()
                return evidence

            monkeypatch.setattr(reader, "read", before_create)
        else:
            service = RuleScoringService(factory, suites, reader)
            evaluate = service.evaluator.evaluate

            async def before_append(*args, **kwargs):
                score = await evaluate(*args, **kwargs)
                await publish_usage()
                return score

            monkeypatch.setattr(service.evaluator, "evaluate", before_append)
        # The automatic lane must survive this exact publication race, while
        # public callers with the old candidate still receive the stale error.
        if guarded:
            with pytest.raises(ValueError, match="scoring_execution_unavailable") as rejected:
                await service.score_batch(scope, batch.id)
            assert type(rejected.value) is ValueError
            assert published
            return
        assert await service.score_batch(scope, batch.id) == 0
        assert published
        if rule_lane:
            with pytest.raises(ValueError, match="scoring_execution_unavailable"):
                await service.score(scope, original)
        else:
            with pytest.raises(ValueError, match="scoring_execution_unavailable"):
                await service.schedule(scope, original, suite.rubric_version, "public-stale")
        assert await service.score_batch(scope, batch.id) == 1
        if rule_lane:
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                assert (
                    await work.evaluation_score.settled(scope, original.result_id, "rule")
                    is not None
                )
        else:
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                judge_run = await work.db_session.scalar(
                    text(
                        "SELECT run_id FROM evaluation_judge_intents WHERE scope_key=:scope AND batch_id=:batch"
                    ),
                    {"scope": "user:" + scope.user_id, "batch": batch.id},
                )
                intent = await work.evaluation_judge.get(scope, judge_run)
                assert intent["status"] == "submitted"
    else:
        await projector.run_once(scope, limit=100)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        revision = await work.db_session.scalar(
            text("SELECT stream_version FROM execution_run_projection WHERE run_id=:run"),
            {"run": original.run_id},
        )
        result_revision = await work.db_session.scalar(
            text("SELECT revision FROM evaluation_batch_results WHERE id=:id"),
            {"id": original.result_id},
        )
    candidate = original.model_copy(
        update={"run_revision": revision, "result_revision": result_revision}
    )
    assert candidate.run_revision > original.run_revision

    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(ValueError, match="scoring_execution_unavailable"):
            await work.evaluation_score.eligible(
                scope,
                principal,
                candidate.model_copy(update={"run_revision": original.run_revision}),
            )
        if intent is not None:
            refreshed, requester = await work.evaluation_judge.current(scope, intent)
            assert refreshed == candidate
            assert requester == principal
            assert (await work.evaluation_judge.get(scope, intent["run_id"]))["candidate"] == (
                candidate if automatic else original
            ).model_dump(mode="json")

    evidence = await RuleEvidenceReader(factory, content_factory=lambda auth: None).read(
        scope, principal, candidate, resources=()
    )
    assert evidence.subject == "answer"
    assert evidence.content_id is not None


@pytest.mark.parametrize(("output", "expected"), [("answer", "valid"), (None, "not_evaluable")])
async def test_real_rule_consumer_published_case_and_complete_output(
    budget_binding_fixture, output, expected
):
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    fixture = await with_rules(
        budget_binding_fixture,
        [{"kind": "text_exact", "expected": "answer"}, {"kind": "text_exact", "expected": "wrong"}],
    )
    suites, scope, _principal, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output=output)
    service = RuleScoringService(
        factory, suites, RuleEvidenceReader(factory, content_factory=lambda auth: None)
    )
    assert await service.score(scope, candidate) == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        scores = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert len(scores) == 2
        assert {s.score.status for s in scores} == {expected}
        assert [s.score.value for s in scores] == ([True, False] if output else [None, None])
        if output:
            assert all(s.score.recording is not None for s in scores)
            assert all(
                any(e.resource_kind == "execution_content" for e in s.score.evidence)
                for s in scores
            )
        assert (await work.evaluation_batch.results(scope, batch.id))[0][
            "scoring_status"
        ] == "pending"


async def test_consumer_rechecks_revocation_after_evidence_read(budget_binding_fixture):
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "text_exact", "expected": "answer"}]
    )
    suites, scope, principal, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output="answer")

    class RevokingReader(RuleEvidenceReader):
        async def read(self, *args, **kwargs):
            result = await super().read(*args, **kwargs)
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                await work.db_session.execute(
                    text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                    {"id": principal.user_id},
                )
                await work.commit()
            return result

    service = RuleScoringService(
        factory, suites, RevokingReader(factory, content_factory=lambda auth: None)
    )
    with pytest.raises(PermissionError):
        await service.score(scope, candidate)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 0


async def test_source_heads_supersedes_cas_and_audit_rollback(budget_binding_fixture):
    from app.domain.evaluation.scoring import ScoreValue
    from app.domain.models.authorization import AuthorizationContext

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, _, batch, candidate = await completed(budget_binding_fixture)
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    applicable = tuple(d.id for d in rubric.dimensions)

    async def append(work, source, value, expected, request, candidate=candidate):
        scores = tuple(
            ScoreValue(
                source=source, dimension=d, rubric_revision=rubric.id, status="valid", value=value
            )
            for d in applicable
        )
        return await work.evaluation_score.append(
            scope,
            principal,
            candidate,
            source=source,
            scores=scores,
            expected_evaluation_revision=expected,
            request_id=request,
            required_dimensions=(),
            applicable_dimensions=applicable,
        )

    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        assert await append(work, "model", 3, 0, "m1") == 1
        await work.commit()
    async with factory(auth) as work:
        candidate = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]
        with pytest.raises(ValueError, match="evaluation_revision_conflict"):
            await append(work, "human", 2, 0, "h0", candidate)
        assert await work.evaluation_score.revision(scope, batch.id) == 1
        assert await append(work, "human", 2, 1, "h1", candidate) == 2
        await work.commit()
    async with factory(auth) as work:
        candidate = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]
        assert await append(work, "model", 4, 2, "m2", candidate) == 3
        await work.commit()
    async with factory(auth) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=3)
        assert len(history) == 3 * len(applicable)
        latest = await work.evaluation_score.heads(scope, batch.id, evaluation_revision=3)
        assert {s.score.source for s in latest} == {"model", "human"}
        assert all(s.score.value == (4 if s.score.source == "model" else 2) for s in latest)
        old = await work.evaluation_score.heads(scope, batch.id, evaluation_revision=1)
        assert all(s.score.value == 3 for s in old)
        assert {s.supersedes_id for s in latest if s.score.source == "model"} == {s.id for s in old}
        candidate = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]

        async def broken_audit(log):
            raise RuntimeError("audit unavailable")

        work.audit.add = broken_audit
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await append(work, "human", 1, 3, "h2", candidate)
        assert await work.evaluation_score.revision(scope, batch.id) == 3
        assert len(
            await work.evaluation_score.history(scope, batch.id, evaluation_revision=3)
        ) == len(history)
        await work.commit()


@pytest.mark.parametrize("change", ["cancel", "result", "run", "unknown", "revoke"])
async def test_stale_candidate_cannot_append(budget_binding_fixture, change):
    from app.domain.models.authorization import AuthorizationContext

    _suites, scope, principal, _suite, _, _, factory = budget_binding_fixture
    _, _, batch, candidate = await completed(budget_binding_fixture)
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        if change == "cancel":
            await work.evaluation_batch.submit(scope, principal, "cancel", "stop", {}, batch.id)
        elif change == "result":
            await work.db_session.execute(
                text("UPDATE evaluation_batch_results SET revision=revision+1")
            )
        elif change == "unknown":
            await work.db_session.execute(
                text("UPDATE evaluation_batch_results SET unknown_effect=true")
            )
        elif change == "revoke":
            await work.db_session.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
        else:
            candidate = candidate.model_copy(update={"run_revision": candidate.run_revision + 1})
        await work.commit()
    async with factory(auth) as work:
        with pytest.raises((ValueError, PermissionError)):
            await work.evaluation_score.append(
                scope,
                principal,
                candidate,
                source="rule",
                scores=(),
                expected_evaluation_revision=0,
                request_id="r",
                required_dimensions=(),
                applicable_dimensions=(),
            )
        assert await work.evaluation_score.revision(scope, batch.id) == 0


async def test_database_rejects_invalid_score_value_even_from_kernel(budget_binding_fixture):
    from sqlalchemy.exc import DBAPIError

    from app.domain.models.authorization import AuthorizationContext

    await test_atomic_immutable_source_revisions_and_pending_model(budget_binding_fixture)
    factory = budget_binding_fixture[-1]
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(DBAPIError, match="score_value_invalid"):
            await work.db_session.execute(
                text(
                    "INSERT INTO evaluation_scores(id,set_id,dimension,status,value,reason,evidence,owner_user_id,team_id,created_by) SELECT :id,set_id,'injected','valid','4'::jsonb,'','[]'::jsonb,owner_user_id,team_id,created_by FROM evaluation_scores LIMIT 1"
                ),
                {"id": uuid4()},
            )


async def test_both_automatic_sources_settle_before_batch_completion(budget_binding_fixture):
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.domain.evaluation.scoring import ScoreValue
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "text_exact", "expected": "wrong"}]
    )
    suites, scope, principal, suite, _, _, factory = fixture
    _, scheduler, batch, candidate = await completed(fixture, final_output="answer")
    consumer = RuleScoringService(
        factory, suites, RuleEvidenceReader(factory, content_factory=lambda auth: None)
    )
    assert await consumer.score_batch(scope, batch.id) == 1
    await scheduler.tick(datetime.now(UTC))
    auth = AuthorizationContext.system("execution-kernel")
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with factory(auth) as work:
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == "running"
        candidate = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]
        dimensions = tuple(d.id for d in rubric.dimensions)
        values = tuple(
            ScoreValue(
                source="model", dimension=d, rubric_revision=rubric.id, status="valid", value=3
            )
            for d in dimensions
        )
        # This exercises the settlement port with controlled judge values, not an E08 judge run.
        await work.evaluation_score.append(
            scope,
            principal,
            candidate,
            source="model",
            scores=values,
            expected_evaluation_revision=1,
            request_id="judge-settlement",
            required_dimensions=(),
            applicable_dimensions=dimensions,
        )
        assert (await work.evaluation_batch.results(scope, batch.id))[0][
            "scoring_status"
        ] == "complete"
        await work.commit()
    await scheduler.tick(datetime.now(UTC))
    async with factory(auth) as work:
        row = await work.evaluation_batch.get(scope, batch.id)
        assert row["status"] == "completed"
        assert row["review_status"] == "not_required"


async def test_concurrent_rule_delivery_appends_once(budget_binding_fixture):
    import asyncio

    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    fixture = await with_rules(
        budget_binding_fixture, [{"kind": "text_exact", "expected": "answer"}]
    )
    suites, scope, _, _, _, _, factory = fixture
    _, _, batch, candidate = await completed(fixture, final_output="answer")
    consumer = RuleScoringService(
        factory, suites, RuleEvidenceReader(factory, content_factory=lambda auth: None)
    )
    outcomes = await asyncio.gather(
        consumer.score(scope, candidate), consumer.score(scope, candidate)
    )
    assert outcomes == [1, 1]
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 1
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM audit_logs WHERE action='evaluation.score.append'")
            )
            == 1
        )
