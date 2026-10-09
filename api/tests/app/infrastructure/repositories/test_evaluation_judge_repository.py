# ruff: noqa: F401,F811
from uuid import uuid4

import pytest

from app.domain.models.authorization import AuthorizationContext
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets
from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def test_durable_judge_intent_is_current_and_idempotent(budget_binding_fixture):
    suites, scope, principal, suite, _config, _pair, factory = budget_binding_fixture
    _, _scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        repo = work.evaluation_judge
        intent = await repo.create(
            scope,
            principal,
            candidate,
            rubric_id=rubric.id,
            config_id=rubric.judge_config_version,
            request_id="judge-first",
            materials={"rubric": [], "evidence": {}, "unavailable": {}},
            namespace_id=batch.id,
        )
        again = await repo.create(
            scope,
            principal,
            candidate,
            rubric_id=rubric.id,
            config_id=rubric.judge_config_version,
            request_id="judge-first",
            materials={"rubric": [], "evidence": {}, "unavailable": {}},
            namespace_id=batch.id,
        )
        assert again["run_id"] == intent["run_id"]
        await work.commit()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (await work.evaluation_judge.get(scope, intent["run_id"]))[
            "candidate"
        ] == candidate.model_dump(mode="json")
        with pytest.raises(ValueError, match="judge_request_conflict"):
            await work.evaluation_judge.create(
                scope,
                principal,
                candidate,
                rubric_id=rubric.id,
                config_id=rubric.judge_config_version,
                request_id="judge-first",
                materials={"changed": True},
                namespace_id=batch.id,
            )


async def test_schedule_admits_dedicated_ask_and_leaves_subject_unchanged(budget_binding_fixture):
    from app.application.evaluation.judge_service import JudgeService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, _principal, suite, config, _pair, factory = budget_binding_fixture
    _, scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    run = await service.schedule(scope, candidate, suite.rubric_version, "judge-schedule")
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, run)
        assert intent["status"] == "submitted"
        assert intent["envelope"]["payload"]["family"] == "ask"
        assert intent["envelope"]["payload"]["semantic_payload"]["judge_protocol"] == 1
        binding = await work.evaluation_budget_control.binding(scope, run)
        assert binding.purpose == "evaluation_judge"
        assert binding.config_version_id != config.id
        assert (await work.evaluation_batch.results(scope, batch.id))[0][
            "run_id"
        ] == candidate.run_id
    assert await service.schedule(scope, candidate, suite.rubric_version, "judge-schedule") == run
    assert await service.score_batch(scope, batch.id) == 0
    assert await service.tick() == 0


async def finish_judge(fixture, scheduler, intent, output, *, late_usage=False):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.application.execution.decisions.base import activity_identity
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunState, decision_data_digest
    from app.domain.models.artifact_provenance import ArtifactProducer
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        actual_handler,
    )

    _, scope, _, _, _, _, factory = fixture
    handler = actual_handler(fixture, scheduler.execution_policy)
    create = CommandEnvelope.model_validate(intent["envelope"])
    assert (await handler.handle(create)).status == "accepted"

    async def command(kind, payload, version=1, command_id=None):
        result = await handler.handle(
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
        assert result.status == "accepted", result

    await command("StartRun", {})
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": intent["run_id"]},
            )
        )
    activity = activity_identity(state, "model:0")
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "model.call",
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "input_ref": "judge/input",
            "input_digest": "a" * 64,
            "input_payload": {"round": 0, "allow_tools": False, "history_refs": []},
        },
    )
    store = PostgresActivityStore(session_factory=handler._session_factory, authorization=auth)
    claim = (
        await store.claim(
            now=datetime.now(UTC), limit=1, worker_id="e08", claim_ttl=timedelta(minutes=5)
        )
    )[0]
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    await command(
        "MarkActivityCallStarted",
        {"activity_id": str(activity), "generation": 0, "claim_generation": claim.claim_generation},
        2,
    )
    if late_usage:
        async with factory(auth) as work:
            config = await work.execution_usage.snapshot(
                scope, intent["run_id"], {"stage": "judge-output-regression"}, "evaluation_judge"
            )
            await work.execution_usage.allocate(
                scope,
                run_id=intent["run_id"],
                activity_id=activity,
                generation=0,
                claim_generation=claim.claim_generation,
                configuration_id=config,
                request_snapshot={"stage": "judge-output-regression"},
            )
            await work.commit()
    writer = ExecutionContentWriter(
        session_factory=handler._session_factory, authorization=auth, objects=None
    )
    command_id = uuid4()
    await writer.record(
        ArtifactProducer(
            scope=scope,
            run_id=intent["run_id"],
            activity_id=activity,
            generation=0,
            claim_generation=claim.claim_generation,
        ),
        command_id=command_id,
        phase="output",
        value={"kind": "model", "message": {"role": "assistant", "content": output}},
    )
    await command(
        "CompleteActivity",
        {
            "activity_id": str(activity),
            "generation": 0,
            "claim_generation": claim.claim_generation,
            "result_ref": "e08/final",
            "result_summary": "truncated",
        },
        2,
        command_id,
    )
    await command("CompleteRun", {"result_ref": "e08/final"})
    await PostgresFormalProjector(
        session_factory=handler._session_factory, authorization=auth
    ).run_once(scope, limit=100)
    if late_usage:
        from app.infrastructure.execution.postgres_execution_usage import ExecutionUsageMaintenance

        assert await ExecutionUsageMaintenance(
            session_factory=handler._session_factory, authorization=auth, handler=handler
        ).process_pending() == {"emitted": 1}
        await PostgresFormalProjector(
            session_factory=handler._session_factory, authorization=auth
        ).run_once(scope, limit=100)


async def test_judge_output_uses_completed_cut_before_late_usage(budget_binding_fixture):
    from sqlalchemy import text

    from app.application.evaluation.judge_service import JudgeService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, _principal, suite, _config, _pair, factory = budget_binding_fixture
    _, scheduler, _batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    run = await service.schedule(scope, candidate, suite.rubric_version, "judge-late-usage-output")
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        intent = await work.evaluation_judge.get(scope, run)
    await finish_judge(budget_binding_fixture, scheduler, intent, "judge answer", late_usage=True)

    async with factory(auth) as work:
        repo = work.evaluation_judge
        projection = await repo.projection(scope, intent)
        assert projection is not None
        terminal = (
            (
                await work.db_session.execute(
                    text("""SELECT stream_version,position FROM execution_events
                      WHERE stream_type='run' AND stream_id=:run AND event_type='RunCompleted'"""),
                    {"run": str(run)},
                )
            )
            .mappings()
            .one()
        )
        assert projection["stream_version"] > terminal["stream_version"]
        assert (
            await work.db_session.scalar(
                text("""SELECT count(*) FROM execution_events WHERE stream_type='run'
                  AND stream_id=:run AND event_type='ModelUsageRecorded'
                  AND stream_version>:terminal"""),
                {"run": str(run), "terminal": terminal["stream_version"]},
            )
            == 1
        )
        assert await repo.output(scope, intent, projection) == "judge answer"
        with pytest.raises(ValueError, match="judge_result_unavailable"):
            await repo.output(
                scope,
                intent,
                {**projection, "stream_version": terminal["stream_version"] - 1},
            )
        with pytest.raises(ValueError, match="judge_result_unavailable"):
            await repo.output(scope, {**intent, "run_id": uuid4()}, projection)


async def test_judge_settlement_refreshes_rule_revision_and_keeps_sources(budget_binding_fixture):
    import json

    from app.application.evaluation.judge_service import JudgeService
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, _principal, suite, _config, _pair, factory = budget_binding_fixture
    _, scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
    service = JudgeService(
        factory, suites, reader, scheduler.admission, execution_policy=scheduler.execution_policy
    )
    run = await service.schedule(scope, candidate, suite.rubric_version, "judge-settle")
    await RuleScoringService(factory, suites, reader).score(scope, candidate)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, run)
    dimensions = [
        {
            "name": d["id"],
            "score": None if d["id"] in intent["materials"]["unavailable"] else 4,
            "reason": "Fixed evidence judged",
            "evidence": [],
        }
        for d in intent["materials"]["rubric"]
    ]
    await finish_judge(
        budget_binding_fixture,
        scheduler,
        intent,
        json.dumps(
            {
                "status": "not_evaluable",
                "dimensions": dimensions,
                "unavailable_reason": "missing_evidence",
            }
        ),
    )
    assert await service.reconcile_batch(scope, batch.id) == 1
    assert await service.reconcile_batch(scope, batch.id) == 0
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_score.revision(scope, batch.id) == 2
        assert await work.evaluation_score.settled(scope, candidate.result_id, "model") == "skipped"
        assert (
            await work.evaluation_score.settled(scope, candidate.result_id, "rule")
            == "not_required"
        )
        assert (await work.evaluation_judge.get(scope, run))["candidate"] == candidate.model_dump(
            mode="json"
        )

    from sqlalchemy import text

    from app.domain.models.scope import OwnerScope

    # Read with the ordinary API login after rule settlement advanced the
    # subject revision. The immutable subject remains the scored provenance;
    # the separate optional UUID identifies only the exact physical judge.
    async with suites.uow_factory(
        AuthorizationContext.for_principal(_principal, scope=scope)
    ) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=2)
        assert history
        assert all(score.run_id == candidate.run_id for score in history)
        model = [score for score in history if score.score.source == "model"]
        assert model
        assert all(score.judge_run_id == run for score in model)
        assert all(score.judge_run_id is None for score in history if score.score.source == "rule")
        assert not await work.db_session.scalar(
            text("SELECT has_table_privilege(current_user,'evaluation_judge_intents','SELECT')")
        )
        assert (
            await work.db_session.scalar(
                text("SELECT public.opencitadel_evaluation_score_judge(:scope,:score)"),
                {"scope": "user:" + str(uuid4()), "score": model[0].source_set_id},
            )
            is None
        )
        assert (
            await work.db_session.scalar(
                text("SELECT public.opencitadel_evaluation_score_judge(:scope,:score)"),
                {"scope": "user:" + scope.user_id, "score": uuid4()},
            )
            is None
        )
    other_scope = OwnerScope.personal(str(uuid4()))
    async with suites.uow_factory(
        AuthorizationContext.for_principal(
            _principal.model_copy(update={"user_id": other_scope.user_id}), scope=other_scope
        )
    ) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT public.opencitadel_evaluation_score_judge(:scope,:score)"),
                {"scope": "user:" + other_scope.user_id, "score": model[0].source_set_id},
            )
            is None
        )


async def test_rescore_allocates_explicit_new_budget_without_changing_original(
    budget_binding_fixture,
):
    from app.application.evaluation.judge_service import JudgeService
    from app.domain.evaluation.judge_protocol import RescoreRequest
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, principal, suite, _config, _pair, factory = budget_binding_fixture
    _, scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    request = RescoreRequest(
        rubric_version=rubric.id,
        judge_config_version=rubric.judge_config_version,
        expected_evaluation_revision=0,
        expected_result_revision=candidate.result_revision,
        token_budget=2000,
        money_budget=None,
    )
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        before = await work.evaluation_budget_control.namespace(scope, batch.id)
    run = await service.rescore(scope, candidate, request, "rescore-first", principal=principal)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, run)
        assert intent["namespace_id"] != batch.id
        assert intent["rescore"]["token_budget"] == 2000
        assert await work.evaluation_budget_control.namespace(scope, batch.id) == before
        assert (
            await work.evaluation_budget_control.namespace(scope, intent["namespace_id"])
        ).token_budget == 2000
    assert (
        await service.rescore(scope, candidate, request, "rescore-first", principal=principal)
        == run
    )
    await service.cancel(scope, principal, run)
    await service.cancel(scope, principal, run)
    assert await service.reconcile_batch(scope, batch.id) == 0
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        stopped = await work.evaluation_judge.get(scope, run)
        assert stopped["status"] == "stopped"
        assert (
            await work.evaluation_budget_control.namespace(scope, stopped["namespace_id"])
        ).state == "closed"
        assert await work.evaluation_budget_control.namespace(scope, batch.id) == before


@pytest.mark.parametrize(
    "scenario",
    [
        "repairs",
        "rescore",
        "unknown",
        "revoked",
        "forged",
        "tool",
        "late_valid",
        "unknown_valid",
        "redacted_runtime",
        "redacted_physical",
        "redacted_settlement",
        "source_revoked",
    ],
)
async def test_actual_model_repairs_have_independent_physical_budget_and_usage(
    budget_binding_fixture, monkeypatch, scenario
):
    import json
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.application.evaluation.judge_runtime import JudgeRuntime
    from app.application.evaluation.judge_service import JudgeService
    from app.application.execution.activities.model_call import ModelCallActivityHandler
    from app.application.execution.decisions.ask import next_ask_command
    from app.application.execution.run_context import run_execution_context
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.evaluation.budget import BudgetPolicy
    from app.domain.execution.activity import ActivityContext
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunState
    from app.domain.models.artifact_provenance import ArtifactProducer
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.external.llm.dispatch import physical_send
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        actual_handler,
    )

    suites, scope, principal, suite, _config, _pair, factory = budget_binding_fixture
    if scenario == "source_revoked":
        from tests.app.execution_test_support import execution_admin_session

        file_id = str(uuid4())
        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "INSERT INTO files(id,owner_user_id,key,content_digest,object_identity) VALUES (:id,:owner,'judge-source',:digest,:object)"
                ),
                {"id": file_id, "owner": principal.user_id, "digest": "a" * 64, "object": uuid4()},
            )
            await db.commit()
        original_record = ExecutionContentWriter.record

        async def with_source(self, producer, **kwargs):
            if kwargs["value"].get("message", {}).get("content") == "answer":
                kwargs["attachment_ids"] = (file_id,)
            return await original_record(self, producer, **kwargs)

        monkeypatch.setattr(ExecutionContentWriter, "record", with_source)
    _, scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        original_budget = await work.evaluation_budget_control.namespace(scope, batch.id)
    if scenario == "rescore":
        from app.domain.evaluation.judge_protocol import RescoreRequest

        rubric = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
        request = RescoreRequest(
            rubric_version=rubric.id,
            judge_config_version=rubric.judge_config_version,
            expected_evaluation_revision=0,
            expected_result_revision=candidate.result_revision,
            token_budget=5000000,
            money_budget=None,
        )
        run = await service.rescore(
            scope, candidate, request, "physical-rescore", principal=principal
        )
    else:
        run = await service.schedule(scope, candidate, suite.rubric_version, "physical-judge")
    policy = BudgetPolicy(
        revision=1, global_concurrency=10, user_concurrency=10, provider_concurrency=10
    )
    async with factory(auth) as work:
        intent = await work.evaluation_judge.get(scope, run)
        await work.evaluation_physical_policy.bootstrap(policy)
        await work.commit()
    handler = actual_handler(budget_binding_fixture, scheduler.execution_policy)
    create = CommandEnvelope.model_validate(intent["envelope"])
    assert (await handler.handle(create)).status == "accepted"

    async def command(kind, payload, version=1, command_id=None):
        result = await handler.handle(
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
        assert result.status == "accepted", result

    await command("StartRun", {})
    monkeypatch.setattr(
        ApiKeyCipher, "decrypt_versioned", lambda self, value: "fake-provider-credential"
    )
    models = InferenceModelService(factory, InfrastructureInferenceProviderAdapter(), None, None)
    dispatch = DurableBudgetDispatchService(
        uow_factory=factory,
        inventory=suites.budgets.inventory,
        physical_policy=policy,
        execution_policy=scheduler.execution_policy,
    )

    @asynccontextmanager
    async def repositories():
        async with factory(auth) as work:
            yield work.execution_usage
            await work.commit()

    usage = ExecutionUsageService(repository_context=repositories, physical_dispatch=dispatch)

    class Objects:
        def __init__(self):
            self.results = {}

        async def load_input(self, **kw):
            return {"message": "judge", "skill_id": "forbidden", "conversation": "forbidden"}

        async def put_result(self, identity, value):
            self.results[str(identity)] = value
            return "e08/" + str(identity)

    objects = Objects()
    sends = []

    # Frozen F06 rows have no redaction mutation port. Inject the current
    # metadata boundary only; all judge/physical/settlement code remains real.
    from app.infrastructure.repositories.db_execution_content_repository import (
        DBExecutionContentRepository,
    )

    get_snapshot = DBExecutionContentRepository.get_snapshot
    redacted = False
    subject_content = intent["materials"]["resources"][0]["resource_id"]

    async def current_metadata(self, owner, content_id, *args, **kwargs):
        row = await get_snapshot(self, owner, content_id, *args, **kwargs)
        if row and redacted and content_id == subject_content:
            return {**row, "redacted": True}
        return row

    monkeypatch.setattr(DBExecutionContentRepository, "get_snapshot", current_metadata)

    async def redact_subject():
        nonlocal redacted
        redacted = True

    class Client:
        def __init__(self, model):
            self.model = model

        async def invoke(self, messages, tools=None):
            assert tools is None
            payload = {
                "model": self.model.model_name,
                "messages": messages,
                "max_completion_tokens": self.model.model.settings.max_output_tokens,
            }

            async def send():
                sends.append(payload)
                return {
                    "model": self.model.model_name,
                    "usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
                }

            if scenario == "redacted_physical":
                await redact_subject()
            await physical_send(send, payload, provider="openai")
            if scenario in {"unknown", "late_valid", "unknown_valid"}:

                async def uncertain_send():
                    sends.append(payload)
                    raise TimeoutError("unknown transport outcome")

                with pytest.raises(TimeoutError):
                    await physical_send(uncertain_send, payload, provider="openai")
            return {
                "content": json.dumps(
                    {
                        "status": "not_evaluable",
                        "unavailable_reason": "missing_evidence",
                        "dimensions": [
                            {
                                "name": d["id"],
                                "score": None
                                if d["id"] in intent["materials"]["unavailable"]
                                else 4,
                                "reason": "Fixed evidence judged",
                                "evidence": [],
                            }
                            for d in intent["materials"]["rubric"]
                        ],
                    }
                )
                if scenario in {"late_valid", "unknown_valid", "redacted_settlement"}
                else "invalid JSON",
                "tool_calls": [{"function": {"name": "attack"}}] if scenario == "tool" else [],
                "_usage": {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
            }

    model = ModelCallActivityHandler(
        objects=objects,
        models=models,
        tools=None,
        judge=JudgeRuntime(factory),
        execution_usage=usage,
        client_factory=lambda model, **kw: Client(model),
    )
    store = PostgresActivityStore(session_factory=handler._session_factory, authorization=auth)
    writer = ExecutionContentWriter(
        session_factory=handler._session_factory, authorization=auth, objects=None
    )
    outcomes = {}
    for ordinal in range(3):
        async with factory(auth) as work:
            state = RunState.model_validate(
                await work.db_session.scalar(
                    text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                    {"run": run},
                )
            )
        plan = next_ask_command(
            state, run_execution_context(state), outcomes=outcomes, now=datetime.now(UTC)
        )
        assert plan.command_type == "RequestActivity"
        assert plan.payload["input_payload"]["round"] == ordinal
        await command(plan.command_type, dict(plan.payload), plan.command_schema_version)
        claim = (
            await store.claim(
                now=datetime.now(UTC), limit=1, worker_id="e08", claim_ttl=timedelta(minutes=5)
            )
        )[0]
        assert await store.mark_call_started(claim, now=datetime.now(UTC))
        await command(
            "MarkActivityCallStarted",
            {
                "activity_id": str(claim.request.activity_id),
                "generation": 0,
                "claim_generation": claim.claim_generation,
            },
            2,
        )
        context = ActivityContext(
            worker_id="e08",
            claim_generation=claim.claim_generation,
            idempotency_key=str(claim.request.activity_id),
            owner_user_id=scope.user_id,
            team_id=None,
            run=run_execution_context(state),
        )
        if ordinal == 1 and scenario in {"unknown", "revoked", "forged"}:
            request = claim.request
            if scenario == "revoked":
                from tests.app.execution_test_support import execution_admin_session

                async with execution_admin_session() as db:
                    await db.execute(
                        text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                        {"id": principal.user_id},
                    )
                    await db.commit()
            if scenario == "forged":
                request = request.model_copy(
                    update={"input_payload": {**request.input_payload, "round": 2}}
                )
            with pytest.raises((ValueError, PermissionError)):
                await model.execute(request, context)
            assert len(sends) == (2 if scenario == "unknown" else 1)
            if scenario == "unknown":
                from app.domain.evaluation.budget import BudgetDemand

                async with factory(auth) as work:
                    assert await work.evaluation_judge.observe_unknown(scope, batch.id) == 0
                    raw = (
                        (
                            await work.db_session.execute(
                                text(
                                    "SELECT r.call_identity,r.demand FROM evaluation_budget_reservations r JOIN execution_model_dispatches d ON d.call_identity=CAST(r.call_identity AS text) AND d.scope_key=r.scope_key WHERE d.run_id=:run AND NOT EXISTS(SELECT 1 FROM execution_model_settlements s WHERE s.scope_key=d.scope_key AND s.call_identity=d.call_identity)"
                                ),
                                {"run": run},
                            )
                        )
                        .mappings()
                        .one()
                    )
                    demand = BudgetDemand.model_validate(raw["demand"])
                    await work.evaluation_budget.mark_unknown(str(raw["call_identity"]), demand)
                    assert await work.evaluation_judge.observe_unknown(scope, batch.id) == 1
                    assert (
                        await work.evaluation_judge.invalidated_sources(
                            scope, batch.id, evaluation_revision=0
                        )
                        == ()
                    )
                    invalid = await work.evaluation_judge.invalidated_sources(
                        scope, batch.id, evaluation_revision=1
                    )
                    assert len(invalid) == 1
                    assert invalid[0].run_id == run
                    await work.commit()
                from app.infrastructure.external.llm.base_llm import normalize_usage

                await dispatch.after_send(
                    scope,
                    str(raw["call_identity"]),
                    normalize_usage(
                        {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
                        provider="openai",
                    ),
                    sends[0]["model"],
                )
                async with factory(auth) as work:
                    assert await work.evaluation_judge.observe_unknown(scope, batch.id) == 0
                    assert (
                        await work.evaluation_judge.invalidated_sources(
                            scope, batch.id, evaluation_revision=1
                        )
                        == invalid
                    )
                with pytest.raises((ValueError, PermissionError), match="judge_effect"):
                    await model.execute(request, context)
                assert len(sends) == 2
            return
        if scenario in {"redacted_runtime", "redacted_physical", "source_revoked"}:
            if scenario == "source_revoked":
                assert any(r["resource_id"] == file_id for r in intent["materials"]["resources"])
                async with execution_admin_session() as db:
                    await db.execute(
                        text("UPDATE files SET content_available=false WHERE id=:id"),
                        {"id": file_id},
                    )
                    await db.commit()
            if scenario == "redacted_runtime":
                from sqlalchemy.exc import DBAPIError

                from tests.app.execution_test_support import execution_admin_session

                async with execution_admin_session() as db:
                    with pytest.raises(DBAPIError, match="execution content is immutable"):
                        await db.execute(
                            text(
                                "UPDATE execution_public_content SET redacted=true WHERE content_id=CAST(:id AS uuid)"
                            ),
                            {"id": subject_content},
                        )
            if scenario == "redacted_runtime":
                await redact_subject()
            with pytest.raises((ValueError, PermissionError)):
                await model.execute(claim.request, context)
            assert sends == []
            await command(
                "FailRun", {"failure_code": "JUDGE_MATERIAL_UNAVAILABLE", "retryable": False}
            )
            await PostgresFormalProjector(
                session_factory=handler._session_factory, authorization=auth
            ).run_once(scope, limit=100)
            assert await service.reconcile_batch(scope, batch.id) == 0
            async with factory(auth) as work:
                assert await work.evaluation_score.revision(scope, batch.id) == 0
            return
        outcome = await model.execute(claim.request, context)
        if scenario == "unknown":
            # A pending physical outcome must block the same planned round's
            # infrastructure retry too, not only the next invalid-output repair.
            async with factory(auth) as work:
                held = RunState.model_validate(
                    await work.db_session.scalar(
                        text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                        {"run": run},
                    )
                )
                with pytest.raises(ValueError, match="judge_effect_unresolved"):
                    await work.evaluation_judge.authorize_run(
                        scope, run, state=held, request=claim.request
                    )
        if scenario == "tool":
            assert outcome.failure_code == "JUDGE_TOOL_CALL_FORBIDDEN"
            assert len(sends) == 1
            async with factory(auth) as work:
                assert (
                    await work.db_session.scalar(
                        text(
                            "SELECT count(*) FROM execution_model_settlements s JOIN execution_model_dispatches d ON d.scope_key=s.scope_key AND d.call_identity=s.call_identity WHERE d.run_id=:run"
                        ),
                        {"run": run},
                    )
                    == 1
                )
            return
        assert outcome.decision_data["judge_status"] == (
            "valid"
            if scenario in {"late_valid", "unknown_valid", "redacted_settlement"}
            else "invalid"
        )
        outcomes[claim.request.activity_id] = dict(outcome.decision_data)
        command_id = uuid4()
        await writer.record(
            ArtifactProducer(
                scope=scope,
                run_id=run,
                activity_id=claim.request.activity_id,
                generation=0,
                claim_generation=claim.claim_generation,
            ),
            command_id=command_id,
            phase="output",
            value=objects.results[str(claim.request.activity_id)],
        )
        await command(
            "CompleteActivity",
            {
                "activity_id": str(claim.request.activity_id),
                "generation": 0,
                "claim_generation": claim.claim_generation,
                "result_ref": outcome.result_ref,
                "decision_data": dict(outcome.decision_data),
            },
            2,
            command_id,
        )
        if scenario == "redacted_settlement":
            await command("CompleteRun", {"result_ref": outcome.result_ref})
            await PostgresFormalProjector(
                session_factory=handler._session_factory, authorization=auth
            ).run_once(scope, limit=100)
            await redact_subject()
            assert await service.reconcile_batch(scope, batch.id) == 0
            async with factory(auth) as work:
                assert await work.evaluation_score.revision(scope, batch.id) == 0
                assert (await work.evaluation_judge.get(scope, run))["status"] == "stopped"
            return
        if scenario in {"late_valid", "unknown_valid"}:
            await command("CompleteRun", {"result_ref": outcome.result_ref})
            await PostgresFormalProjector(
                session_factory=handler._session_factory, authorization=auth
            ).run_once(scope, limit=100)
            async with factory(auth) as work:
                identity = await work.db_session.scalar(
                    text(
                        "SELECT d.call_identity FROM execution_model_dispatches d WHERE d.run_id=:run AND NOT EXISTS(SELECT 1 FROM execution_model_settlements s WHERE s.scope_key=d.scope_key AND s.call_identity=d.call_identity)"
                    ),
                    {"run": run},
                )
                assert identity
                before = await work.evaluation_score.revision(scope, batch.id)
            if scenario == "unknown_valid":
                await dispatch.mark_unknown(scope, identity)
            assert await service.reconcile_batch(scope, batch.id) == 0
            async with factory(auth) as work:
                assert (
                    await work.evaluation_score.history(
                        scope, batch.id, evaluation_revision=before + (scenario == "unknown_valid")
                    )
                    == ()
                )
                if scenario == "late_valid":
                    assert (await work.evaluation_judge.get(scope, run))["status"] == "submitted"
                    assert await work.evaluation_score.revision(scope, batch.id) == before
            from app.infrastructure.external.llm.base_llm import normalize_usage

            await dispatch.after_send(
                scope,
                identity,
                normalize_usage(
                    {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40},
                    provider="openai",
                ),
                sends[0]["model"],
            )
            assert await service.reconcile_batch(scope, batch.id) == (
                1 if scenario == "late_valid" else 0
            )
            async with factory(auth) as work:
                history = await work.evaluation_score.history(
                    scope, batch.id, evaluation_revision=before + 1
                )
                if scenario == "late_valid":
                    assert history
                    assert any(s.score.value == 4 for s in history)
                    assert all(s.score.status != "error" for s in history)
                else:
                    assert history == ()
                    assert await work.evaluation_judge.unsafe(
                        scope, intent, include_unresolved=False
                    )
            return
    async with factory(auth) as work:
        state = RunState.model_validate(
            await work.db_session.scalar(
                text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                {"run": run},
            )
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": run},
            )
            == 3
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT count(*) FROM execution_model_settlements s JOIN execution_model_dispatches d ON d.scope_key=s.scope_key AND d.call_identity=s.call_identity WHERE d.run_id=:run"
                ),
                {"run": run},
            )
            == 3
        )
        calls = (
            (
                await work.db_session.execute(
                    text("SELECT sends FROM evaluation_model_logical_calls WHERE run_id=:run"),
                    {"run": run},
                )
            )
            .scalars()
            .all()
        )
        assert calls == [1, 1, 1]
        if scenario == "rescore":
            assert (
                await work.evaluation_budget_control.namespace(scope, batch.id) == original_budget
            )
            demands = (
                (
                    await work.db_session.execute(
                        text(
                            "SELECT r.demand FROM evaluation_budget_reservations r JOIN execution_model_dispatches d ON d.call_identity=CAST(r.call_identity AS text) AND d.scope_key=r.scope_key WHERE d.run_id=:run"
                        ),
                        {"run": run},
                    )
                )
                .scalars()
                .all()
            )
            assert len(demands) == 3
            for demand in demands:
                assert demand["batch_id"] == str(batch.id)
                keys = {b["key"] for b in demand["buckets"]}
                assert "5:batch:" + str(intent["namespace_id"]) in keys
                assert "6:purpose:" + str(batch.id) + ":evaluation_judge" in keys
    plan = next_ask_command(
        state, run_execution_context(state), outcomes=outcomes, now=datetime.now(UTC)
    )
    assert plan.command_type == "FailRun"
    assert plan.payload["retryable"] is False
    assert len(sends) == 3
    await command(plan.command_type, dict(plan.payload), plan.command_schema_version)
    await PostgresFormalProjector(
        session_factory=handler._session_factory, authorization=auth
    ).run_once(scope, limit=100)
    assert await service.reconcile_batch(scope, batch.id) == 1
    async with factory(auth) as work:
        assert await work.evaluation_score.settled(scope, candidate.result_id, "model") == "failed"
        scores = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert all(s.score.value is None for s in scores)


async def test_cancel_withdraws_pending_judge_create_before_namespace_close(budget_binding_fixture):
    from datetime import UTC, datetime

    from app.application.evaluation.judge_service import JudgeService
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    batches, scheduler, batch, candidate = await completed(
        budget_binding_fixture, final_output="answer"
    )
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    run = await service.schedule(scope, candidate, suite.rubric_version, "judge-cancel")
    await batches.cancel(scope, principal, "cancel-judge-parent", {"batch_id": str(batch.id)})
    await scheduler.tick(datetime.now(UTC))
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, run)
        assert intent["status"] == "stopped"
        assert (await work.evaluation_budget_control.namespace(scope, batch.id)).state == "closed"
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == "cancelled"


async def test_rescore_new_rubric_preserves_history_and_terminal_execution(budget_binding_fixture):
    import json
    from datetime import UTC, datetime

    from app.application.evaluation.judge_service import JudgeService
    from app.application.evaluation.rule_scoring_service import RuleScoringService
    from app.domain.evaluation.judge_protocol import RescoreRequest
    from app.domain.evaluation.rubric import RubricDefinition
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    _, scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
    service = JudgeService(
        factory, suites, reader, scheduler.admission, execution_policy=scheduler.execution_policy
    )
    await RuleScoringService(factory, suites, reader).score(scope, candidate)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        candidate = (await work.evaluation_batch.scoring_candidates(scope, batch.id))[0]

    async def settle(run):
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            intent = await work.evaluation_judge.get(scope, run)
        dims = [
            {
                "name": d["id"],
                "score": None if d["id"] in intent["materials"]["unavailable"] else 4,
                "reason": "fixed rubric",
                "evidence": [],
            }
            for d in intent["materials"]["rubric"]
        ]
        await finish_judge(
            budget_binding_fixture,
            scheduler,
            intent,
            json.dumps(
                {
                    "status": "not_evaluable",
                    "dimensions": dims,
                    "unavailable_reason": "missing_evidence",
                }
            ),
        )
        assert await service.reconcile_batch(scope, batch.id) == 1

    original_judge = await service.schedule(scope, candidate, suite.rubric_version, "initial")
    await settle(original_judge)
    await scheduler.tick(datetime.now(UTC))
    old = await suites.get_version(scope, principal, "rubric", suite.rubric_version)
    definition = RubricDefinition(**{k: getattr(old, k) for k in RubricDefinition.model_fields})
    draft = await suites.create(
        scope,
        principal,
        kind="rubric",
        name="Revised rubric",
        definition=definition.model_dump(mode="json"),
        request_id=str(uuid4()),
    )
    revised = await suites.publish(
        scope,
        principal,
        kind="rubric",
        entity_id=draft.id,
        expected_revision=1,
        request_id=str(uuid4()),
    )
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        before = await work.evaluation_score.history(scope, batch.id, evaluation_revision=2)
        row = (await work.evaluation_batch.results(scope, batch.id))[0]
        candidate = candidate.model_copy(update={"result_revision": row["revision"]})
        status = (await work.evaluation_batch.get(scope, batch.id))["status"]
    request = RescoreRequest(
        rubric_version=revised.id,
        judge_config_version=revised.judge_config_version,
        expected_evaluation_revision=2,
        expected_result_revision=candidate.result_revision,
        token_budget=20000,
        money_budget=None,
    )
    await settle(
        await service.rescore(scope, candidate, request, "new-rubric", principal=principal)
    )
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert await work.evaluation_score.history(scope, batch.id, evaluation_revision=2) == before
        after = await work.evaluation_score.history(scope, batch.id, evaluation_revision=3)
        new = [s for s in after if s.evaluation_revision == 3]
        assert new
        assert all(s.score.rubric_revision == revised.id and s.supersedes_id is None for s in new)
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == status

        # Physical unknown/late settlement is exercised above. Here isolate the
        # observer's exact source-set association across two rubric revisions.
        async def original_unknown(scope, intent, *, include_unresolved=True):
            assert include_unresolved is False
            return intent["run_id"] == original_judge

        work.evaluation_judge.unsafe = original_unknown
        assert await work.evaluation_judge.observe_unknown(scope, batch.id) == 1
        assert await work.evaluation_judge.observe_unknown(scope, batch.id) == 0
        assert not await work.evaluation_judge.invalidated_sources(
            scope, batch.id, evaluation_revision=3
        )
        invalid = await work.evaluation_judge.invalidated_sources(
            scope, batch.id, evaluation_revision=4
        )
        assert len(invalid) == 1
        assert invalid[0].run_id == original_judge
        assert invalid[0].source_set_id is not None
        from sqlalchemy import text

        source = (
            (
                await work.db_session.execute(
                    text("SELECT rubric_revision,source FROM evaluation_score_sets WHERE id=:id"),
                    {"id": invalid[0].source_set_id},
                )
            )
            .mappings()
            .all()
        )
        assert source
        assert all(
            row["rubric_revision"] == suite.rubric_version and row["source"] == "model"
            for row in source
        )
        assert await work.evaluation_score.history(scope, batch.id, evaluation_revision=3) == after
        await work.commit()


@pytest.mark.parametrize("cancel_mode", ["batch", "judge", "revoked"])
async def test_actual_judge_cancel_waits_for_accepted_child_terminal(
    budget_binding_fixture, cancel_mode
):
    from datetime import UTC, datetime

    from sqlalchemy import text

    from app.application.evaluation.judge_service import JudgeService
    from app.domain.execution.commands import CommandEnvelope
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        actual_handler,
    )

    suites, scope, principal, suite, _, _, factory = budget_binding_fixture
    batches, scheduler, batch, candidate = await completed(
        budget_binding_fixture, final_output="answer"
    )
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    run = await service.schedule(scope, candidate, suite.rubric_version, "running-cancel")
    auth = AuthorizationContext.system("execution-kernel")
    async with factory(auth) as work:
        intent = await work.evaluation_judge.get(scope, run)
    handler = actual_handler(budget_binding_fixture, scheduler.execution_policy)
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
    if cancel_mode == "batch":
        await batches.cancel(scope, principal, "cancel-running-parent", {"batch_id": str(batch.id)})
        await scheduler.tick(datetime.now(UTC))
        await scheduler.tick(datetime.now(UTC))
    elif cancel_mode == "judge":
        await service.cancel(scope, principal, run)
        await service.cancel(scope, principal, run)
    else:
        from tests.app.execution_test_support import execution_admin_session

        async with execution_admin_session() as db:
            await db.execute(
                text("UPDATE users SET token_version=token_version+1 WHERE id=:id"),
                {"id": principal.user_id},
            )
            await db.commit()
        await service.reconcile_batch(scope, batch.id)
        await service.reconcile_batch(scope, batch.id)
    async with factory(auth) as work:
        assert (await work.evaluation_batch.get(scope, batch.id))["status"] == (
            "cancelling" if cancel_mode == "batch" else "running"
        )
        assert (await work.evaluation_budget_control.namespace(scope, batch.id)).state == "open"
        with pytest.raises(
            (ValueError, PermissionError), match=r"judge_cancelled|authorization|principal"
        ):
            await work.evaluation_judge.authorize_run(scope, run)
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT command_id,payload FROM execution_command_inbox WHERE stream_id=:run AND command_type='CancelRun'"
                    ),
                    {"run": str(run)},
                )
            )
            .mappings()
            .one()
        )
    cancel = envelope.model_copy(
        update={
            "command_id": row["command_id"],
            "command_type": "CancelRun",
            "expected_stream_version": None,
            "payload": row["payload"],
        }
    )
    assert (await handler.handle(cancel)).status == "accepted"
    await PostgresFormalProjector(
        session_factory=handler._session_factory, authorization=auth
    ).run_once(scope, limit=100)
    await service.reconcile_batch(scope, batch.id)
    await scheduler.tick(datetime.now(UTC))
    async with factory(auth) as work:
        assert (await work.evaluation_judge.get(scope, run))["status"] == "stopped"
        assert (await work.evaluation_budget_control.namespace(scope, batch.id)).state == "closed"


@pytest.fixture
async def team_judge_fixture(configurations, isolated_database):
    # Build the existing real publication/C1 fixture in a fresh team scope.
    from types import SimpleNamespace

    from sqlalchemy import text

    from app.domain.models.scope import OwnerScope, Principal
    from tests.app.application.services.test_artifact_provenance_postgres import seed
    from tests.app.execution_test_support import execution_admin_session

    suites, dataset_service, _, original = configurations
    other, _ = await seed()
    team = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
        for user in (original.user_id, other):
            await db.execute(
                text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
                {"team": team, "user": user},
            )
        await db.commit()
    from app.domain.models.team import TeamRole

    original = original.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
    caller = Principal(user_id=other, team_roles={team: "member"})
    scope = OwnerScope.team(original.user_id, team)
    generator = budget_binding_fixture.__wrapped__(
        (suites, dataset_service, scope, original), isolated_database, SimpleNamespace(param={})
    )
    fixture = await anext(generator)
    try:
        yield fixture, caller
    finally:
        await generator.aclose()


@pytest.mark.parametrize("revocation", [None, "before_create", "before_allocation"])
async def test_team_rescore_retains_actual_budget_authorizer(
    team_judge_fixture, monkeypatch, revocation
):
    from sqlalchemy import text

    from app.application.evaluation.judge_service import JudgeService
    from app.domain.evaluation.judge_protocol import RescoreRequest
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.repositories.db_evaluation_judge_repository import (
        DBEvaluationJudgeRepository,
    )
    from tests.app.execution_test_support import execution_admin_session

    fixture, caller = team_judge_fixture
    suites, scope, original, suite, _, _, factory = fixture
    _, scheduler, batch, candidate = await completed(fixture, final_output="answer")
    reader = RuleEvidenceReader(factory, content_factory=lambda auth: None)
    service = JudgeService(
        factory, suites, reader, scheduler.admission, execution_policy=scheduler.execution_policy
    )
    rubric = await suites.get_version(scope, original, "rubric", suite.rubric_version)
    request = RescoreRequest(
        rubric_version=rubric.id,
        judge_config_version=rubric.judge_config_version,
        expected_evaluation_revision=0,
        expected_result_revision=candidate.result_revision,
        token_budget=2000,
        money_budget=None,
    )

    async def revoke():
        async with execution_admin_session() as db:
            await db.execute(
                text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
                {"team": scope.team_id, "user": caller.user_id},
            )
            await db.commit()

    if revocation == "before_create":
        original_read = reader.read

        async def read(*args, **kwargs):
            evidence = await original_read(*args, **kwargs)
            await revoke()
            return evidence

        monkeypatch.setattr(reader, "read", read)
    if revocation == "before_allocation":
        admit = service.admission.admit

        async def delayed_admit(*args, **kwargs):
            await revoke()
            return await admit(*args, **kwargs)

        monkeypatch.setattr(service.admission, "admit", delayed_admit)
    caller_scope = scope.model_copy(update={"user_id": caller.user_id})
    if revocation:
        with pytest.raises(PermissionError, match="revoked"):
            await service.rescore(
                caller_scope, candidate, request, "team-rescore", principal=caller
            )
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            assert (
                await work.db_session.scalar(
                    text(
                        "SELECT count(*) FROM evaluation_budget_namespaces WHERE scope_key=:scope AND id<>:batch"
                    ),
                    {"scope": "team:" + scope.team_id, "batch": batch.id},
                )
                == 0
            )
        return
    run = await service.rescore(caller_scope, candidate, request, "team-rescore", principal=caller)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, run)
        assert intent["authorizer"] == caller.model_dump(mode="json")
        assert (await work.evaluation_batch.get(scope, batch.id))[
            "principal"
        ] == original.model_dump(mode="json")
        actor = await work.db_session.scalar(
            text(
                "SELECT actor_user_id FROM audit_logs WHERE action='evaluation.rescore.authorize' AND resource_id=:result"
            ),
            {"result": str(candidate.result_id)},
        )
        assert actor == caller.user_id
        assert intent["status"] == "submitted"
    assert (
        await service.rescore(caller_scope, candidate, request, "team-rescore", principal=caller)
        == run
    )
    await revoke()
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(PermissionError, match="revoked"):
            await work.evaluation_judge.authorize_run(scope, run)


async def test_dimension_citations_persist_exact_fixed_resources(budget_binding_fixture):
    import json
    from dataclasses import replace

    from sqlalchemy import text

    from app.application.evaluation.judge_service import JudgeService
    from app.domain.evaluation.rule_engine import ArtifactEvidence
    from app.domain.models.resource_pin import ResourceIdentity
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from tests.app.infrastructure.repositories.test_e07_reader_integration import tool_output

    suites, scope, _principal, suite, _, _, factory = budget_binding_fixture

    async def sources(row, handler, command):
        await tool_output(scope, row, handler, command, body={"source": "first"})
        await tool_output(scope, row, handler, command, body={"source": "second"})

    _, scheduler, batch, candidate = await completed(
        budget_binding_fixture, final_output="answer", before_model=sources
    )

    class FixedSources(RuleEvidenceReader):
        # Supply two independently authorized real F06 bodies to isolate the
        # citation-to-ScoreValue boundary from automatic source selection.
        async def read(self, owner, actor, current, *, resources):
            result = await super().read(owner, actor, current, resources=resources)
            async with factory(AuthorizationContext.system("execution-kernel")) as work:
                rows = (
                    (
                        await work.db_session.execute(
                            text(
                                "SELECT c.content_id,c.content_digest,b.step_id,b.formal_position FROM execution_public_content c JOIN execution_content_bindings b USING(content_id) WHERE b.run_id=:run AND c.body::jsonb->>'kind'='tool' ORDER BY b.formal_position"
                            ),
                            {"run": current.run_id},
                        )
                    )
                    .mappings()
                    .all()
                )
            fixed = []
            for row in rows:
                resource = ResourceIdentity(
                    resource_kind="execution_content",
                    resource_id=str(row["content_id"]),
                    resource_version=row["content_digest"],
                )
                body = await self._body(owner, actor, current, row)
                fixed.append(ArtifactEvidence(resource, "fixed_source", body))
            assert len(fixed) == 2
            return replace(
                result,
                sources=tuple(fixed),
                resources=(*result.resources, *(a.resource for a in fixed)),
            )

    service = JudgeService(
        factory,
        suites,
        FixedSources(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    run = await service.schedule(scope, candidate, suite.rubric_version, "precise-citations")
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        intent = await work.evaluation_judge.get(scope, run)
    refs = {"correctness": ["source:1"], "source_support": ["source:0"], "completeness": []}
    output = json.dumps(
        {
            "status": "complete",
            "unavailable_reason": None,
            "dimensions": [
                {
                    "name": d["id"],
                    "score": 4,
                    "reason": "Only cited fixed source supports this dimension",
                    "evidence": refs[d["id"]],
                }
                for d in intent["materials"]["rubric"]
            ],
        }
    )
    await finish_judge(budget_binding_fixture, scheduler, intent, output)
    assert await service.reconcile_batch(scope, batch.id) == 1
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        history = await work.evaluation_score.history(scope, batch.id, evaluation_revision=1)
        assert len(history) == 3
        for saved in history:
            expected = tuple(
                ResourceIdentity.model_validate(intent["materials"]["evidence"][key]["resource"])
                for key in refs[saved.score.dimension]
            )
            assert saved.score.status == "valid"
            assert saved.score.evidence == expected
        assert len((await work.evaluation_judge.get(scope, run))["materials"]["resources"]) == 3


@pytest.mark.parametrize("stage", ["pending", "running", "terminal"])
async def test_transient_subject_hold_preserves_retryable_judge(
    budget_binding_fixture, monkeypatch, stage
):
    import json

    from app.application.evaluation.judge_service import JudgeService
    from app.domain.evaluation.execution_slots import ExecutionCapacityUnavailable
    from app.domain.execution.commands import CommandEnvelope
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.repositories.db_evaluation_score_repository import (
        DBEvaluationScoreRepository,
    )
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        actual_handler,
    )

    suites, scope, _, suite, _, _, factory = budget_binding_fixture
    _, scheduler, batch, candidate = await completed(budget_binding_fixture, final_output="answer")
    service = JudgeService(
        factory,
        suites,
        RuleEvidenceReader(factory, content_factory=lambda auth: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    admit = service._admit
    if stage == "pending":

        async def capacity(*args, **kwargs):
            raise ExecutionCapacityUnavailable("busy")

        monkeypatch.setattr(service, "_admit", capacity)
        with pytest.raises(ExecutionCapacityUnavailable):
            await service.schedule(scope, candidate, suite.rubric_version, "transient-source")
        monkeypatch.setattr(service, "_admit", admit)
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            intent = (await work.evaluation_judge.active(scope, batch.id))[0]
    else:
        run = await service.schedule(scope, candidate, suite.rubric_version, "transient-source")
        async with factory(AuthorizationContext.system("execution-kernel")) as work:
            intent = await work.evaluation_judge.get(scope, run)
        if stage == "running":
            handler = actual_handler(budget_binding_fixture, scheduler.execution_policy)
            assert (
                await handler.handle(CommandEnvelope.model_validate(intent["envelope"]))
            ).status == "accepted"
        else:
            output = json.dumps(
                {
                    "status": "not_evaluable",
                    "unavailable_reason": "missing",
                    "dimensions": [
                        {
                            "name": d["id"],
                            "score": None if d["id"] in intent["materials"]["unavailable"] else 4,
                            "reason": "fixed",
                            "evidence": [],
                        }
                        for d in intent["materials"]["rubric"]
                    ],
                }
            )
            await finish_judge(budget_binding_fixture, scheduler, intent, output)
    eligible = DBEvaluationScoreRepository.eligible

    async def temporary(*args, **kwargs):
        raise ValueError("scoring_effect_unresolved")

    monkeypatch.setattr(DBEvaluationScoreRepository, "eligible", temporary)
    assert await service.reconcile_batch(scope, batch.id) == 0
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        saved = await work.evaluation_judge.get(scope, intent["run_id"])
        assert saved["status"] == ("pending" if stage == "pending" else "submitted")
        assert saved["cancel_envelope"] is None
        assert await work.evaluation_score.revision(scope, batch.id) == 0
    monkeypatch.setattr(DBEvaluationScoreRepository, "eligible", eligible)
    assert await service.reconcile_batch(scope, batch.id) == (1 if stage == "terminal" else 0)
    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        assert (await work.evaluation_judge.get(scope, intent["run_id"]))["status"] == (
            "settled" if stage == "terminal" else "submitted"
        )
