# ruff: noqa: F401,F811
"""E08 replaces the pre-intent E05 judge fixture, retaining actual shared-cap assertions."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.infrastructure.repositories.test_evaluation_physical_dispatch import (
    budget_binding_fixture,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
)
from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize(
    ("budget_binding_fixture", "exhausted"),
    [
        ({"token_budget": 3000000}, "budget_exhausted"),
        (
            {
                "token_budget": 5000000,
                "money_budget": 2.5,
                "price": {
                    "input_per_million": "1",
                    "output_per_million": "2",
                    "cache_read_per_million": "0.1",
                    "cache_write_per_million": "1",
                    "reasoning_uses_output_rate": True,
                },
            },
            "budget_money_exhausted",
        ),
    ],
    indirect=["budget_binding_fixture"],
)
async def test_subject_and_judge_actual_sends_exhaust_one_total_with_separate_f07_purpose(
    budget_binding_fixture, exhausted, monkeypatch
):
    from decimal import Decimal
    from unittest.mock import AsyncMock

    from openai.types.chat import ChatCompletion

    from app.application.evaluation.judge_service import JudgeService
    from app.application.execution.decisions.base import activity_identity
    from app.application.execution.run_context import run_execution_context
    from app.application.ports.inference_dispatch import dispatch_context
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.errors import ServerRequestsError
    from app.domain.evaluation.budget import BudgetPolicy
    from app.domain.evaluation.execution_slots import ExecutionSlotPolicy
    from app.domain.execution.activity import ActivityContext
    from app.domain.execution.commands import CommandEnvelope
    from app.domain.execution.run import RunState
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.external.llm.openai_llm import OpenAILLM
    from app.infrastructure.security.api_key_cipher import ApiKeyCipher
    from tests.app.infrastructure.repositories.test_evaluation_batch_repository import (
        actual_handler,
    )

    fixture = budget_binding_fixture
    suites, scope, _, suite, _, _, factory = fixture
    auth = AuthorizationContext.system("execution-kernel")
    physical = BudgetPolicy(
        revision=1, global_concurrency=10, user_concurrency=10, provider_concurrency=10
    )
    execution = ExecutionSlotPolicy(revision=1)
    async with factory(auth) as work:
        await work.evaluation_physical_policy.bootstrap(physical)
        await work.commit()
    monkeypatch.setattr(
        ApiKeyCipher, "decrypt_versioned", lambda self, value: "fake-provider-credential"
    )
    models = InferenceModelService(factory, InfrastructureInferenceProviderAdapter(), None, None)
    model = await models.resolve_chat("e02-model", scope=scope)
    durable = DurableBudgetDispatchService(
        uow_factory=factory,
        inventory=suites.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    sent = []

    async def create(**payload):
        sent.append(payload)
        return ChatCompletion(
            id="fake",
            created=0,
            object="chat.completion",
            model=model.model_name,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "OK"},
                }
            ],
            usage={
                "prompt_tokens": 1000000,
                "completion_tokens": 2,
                "total_tokens": 1000002,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        )

    monkeypatch.setattr(
        "app.infrastructure.external.llm.openai_llm.AsyncOpenAI",
        lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=AsyncMock()
        ),
    )
    monkeypatch.setattr(
        "app.infrastructure.external.llm.openai_llm.create_ssrf_safe_async_client",
        lambda **kw: None,
    )
    adapter = OpenAILLM(model)

    async def activity(run, handler, command):
        async with factory(auth) as work:
            state = RunState.model_validate(
                await work.db_session.scalar(
                    text("SELECT state FROM evaluation_execution_leases WHERE run_id=:run"),
                    {"run": run},
                )
            )
        identity = activity_identity(state, "model:0")
        await command(
            "RequestActivity",
            {
                "activity_id": str(identity),
                "activity_type": "model.call",
                "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                "input_ref": "fixed-input",
                "input_digest": "a" * 64,
                "input_payload": {"round": 0, "history_refs": [], "allow_tools": False},
            },
            2,
        )
        store = PostgresActivityStore(session_factory=handler._session_factory, authorization=auth)
        claim = (
            await store.claim(
                now=datetime.now(UTC),
                limit=1,
                worker_id="e08-shared",
                claim_ttl=timedelta(minutes=5),
            )
        )[0]
        assert await store.mark_call_started(claim, now=datetime.now(UTC))
        await command(
            "MarkActivityCallStarted",
            {
                "activity_id": str(identity),
                "generation": 0,
                "claim_generation": claim.claim_generation,
            },
            2,
        )
        context = ActivityContext(
            worker_id="e08-shared",
            claim_generation=claim.claim_generation,
            idempotency_key=str(identity),
            owner_user_id=scope.user_id,
            team_id=None,
            run=run_execution_context(state),
        )
        return claim, usage.guard(
            scope=scope,
            request=claim.request,
            context=context,
            purpose="production",
            resolved={"policy_revision": "untrusted-hint", "tool_fingerprint": None},
        )

    async def subject_send(row, handler, command):
        claim, guard = await activity(row["run_id"], handler, command)
        with dispatch_context(guard, model):
            await adapter.invoke([{"role": "user", "content": "actual subject request"}])
        await command(
            "CompleteActivity",
            {
                "activity_id": str(claim.request.activity_id),
                "generation": 0,
                "claim_generation": claim.claim_generation,
                "result_ref": "subject/provider",
            },
            2,
        )

    try:
        _, scheduler, _batch, candidate = await completed(
            fixture, final_output="OK", before_model=subject_send
        )
        judges = JudgeService(
            factory,
            suites,
            RuleEvidenceReader(factory, content_factory=lambda auth: None),
            scheduler.admission,
            execution_policy=execution,
        )
        run = await judges.schedule(scope, candidate, suite.rubric_version, "shared-budget-judge")
        async with factory(auth) as work:
            intent = await work.evaluation_judge.get(scope, run)
            subject = await work.evaluation_budget_control.binding(scope, candidate.run_id)
            judge = await work.evaluation_budget_control.binding(scope, run)
        assert judge.namespace_id == subject.namespace_id
        assert judge.config_version_id != subject.config_version_id
        handler = actual_handler(fixture, execution)
        envelope = CommandEnvelope.model_validate(intent["envelope"])
        assert (await handler.handle(envelope)).status == "accepted"

        async def command(kind, payload, version=1):
            result = await handler.handle(
                envelope.model_copy(
                    update={
                        "command_id": uuid4(),
                        "command_type": kind,
                        "command_schema_version": version,
                        "expected_stream_version": None,
                        "payload": payload,
                    }
                )
            )
            assert result.status == "accepted", result

        await command("StartRun", {})
        _, guard = await activity(run, handler, command)
        with dispatch_context(guard, model):
            await adapter.invoke([{"role": "user", "content": "actual judge request"}])
        with dispatch_context(guard, model), pytest.raises(ServerRequestsError, match=exhausted):
            await adapter.invoke([{"role": "user", "content": "must not send"}])
    finally:
        await adapter.aclose()
    assert len(sent) == 2
    async with factory(auth) as work:
        purposes = (
            await work.db_session.execute(
                text(
                    "SELECT purpose,body->>'evaluation_configuration_version_id' AS version FROM execution_configurations WHERE body ? 'budget_logical_invocation' ORDER BY purpose"
                )
            )
        ).all()
        assert purposes == [
            ("evaluation_judge", str(judge.config_version_id)),
            ("evaluation_subject", str(subject.config_version_id)),
        ]
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 2
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 2
        )
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT spent_tokens,reserved_tokens,spent_money,slots FROM evaluation_budget_buckets WHERE key=:key"
                    ),
                    {"key": "5:batch:" + str(subject.namespace_id)},
                )
            )
            .mappings()
            .one()
        )
        assert row["spent_tokens"] == 2000004
        assert row["reserved_tokens"] == 0
        assert row["slots"] == 0
        if exhausted == "budget_money_exhausted":
            assert row["spent_money"] == Decimal("2.000008")
