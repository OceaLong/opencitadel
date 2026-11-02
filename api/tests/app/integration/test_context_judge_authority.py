# ruff: noqa: F401,F811 -- owned database fixture dependencies
"""Restricted judges prepare authorized materials and send through the real budget gate."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from redis.asyncio import Redis
from sqlalchemy import text

from app.application.evaluation.judge_runtime import JudgeRuntime
from app.application.evaluation.judge_service import JudgeService
from app.application.execution.activities.model_call import ModelCallActivityHandler
from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.activity_registry import ActivityRegistry
from app.application.execution.context_compiler import compile_context
from app.application.execution.decisions import DECISION_PLANNERS
from app.application.execution.definition_registry import DefinitionRegistry
from app.application.services.execution_usage_service import ExecutionUsageService
from app.application.services.inference_model_service import InferenceModelService
from app.composition.context_runtime import build_context_runtime
from app.composition.kernel_runtime import build_execution_kernel_runtime
from app.domain.evaluation.budget import BudgetPolicy
from app.domain.execution.context_models import ContextPolicy
from app.domain.execution.definition import HandlerImplementation
from app.domain.execution.family import RunFamily
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
from app.infrastructure.evaluation.rule_evidence_reader import RuleEvidenceReader
from app.infrastructure.execution.admission_definition_resolver import AdmissionDefinitionResolver
from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
from app.infrastructure.execution.evaluation_execution import EvaluationExecutionGuard
from app.infrastructure.execution.postgres_definition_store import PostgresDefinitionStore
from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher
from core.config import load_deployment_settings
from tests.app.application.execution.test_context_objects import Storage
from tests.app.execution_test_support import authenticated_session_factory
from tests.app.infrastructure.execution.test_definition_lifecycle import manifest
from tests.app.infrastructure.repositories.test_evaluation_physical_dispatch import (
    budget_binding_fixture,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
)
from tests.app.infrastructure.repositories.test_evaluation_score_repository import completed

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.mark.parametrize("revoke", [False, True])
@pytest.mark.parametrize("output_tokens", [8192, 4096])
async def test_judge_materials_are_protected_and_revalidated_before_native_send(
    budget_binding_fixture, monkeypatch, revoke, output_tokens
):
    suites, scope, _, suite, _, _, uow = budget_binding_fixture
    _, scheduler, _, candidate = await completed(
        budget_binding_fixture, final_output="exact answer"
    )
    auth = AuthorizationContext.system("execution-kernel")
    settings = load_deployment_settings()
    sessions = authenticated_session_factory(
        uow().session_factory.kw["bind"],
        signing_secret=settings.database_authorization_signing_secret,
    )

    class OwnedStorage(Storage):
        async def get_bytes(self, key):
            return self.objects[key]

    storage = OwnedStorage()
    objects = ActivityObjectStore(storage)
    definition = manifest(
        family=RunFamily.ASK,
        context_policy=ContextPolicy(main_output_tokens=output_tokens),
        handlers=tuple(
            HandlerImplementation(activity_type=kind, handler_version="1")
            for kind in ("context.prepare", "context.compact", "model.call")
        ),
    )
    definitions = DefinitionRegistry()
    definitions.register(
        definition, planner=DECISION_PLANNERS[RunFamily.ASK].planner, compiler=compile_context
    )
    physical = BudgetPolicy(
        revision=1, global_concurrency=10, user_concurrency=10, provider_concurrency=10
    )
    async with uow(auth) as work:
        await PostgresDefinitionStore(work.db_session).seed_head(definition)
        await work.evaluation_physical_policy.bootstrap(physical)
        await work.commit()
    scheduler.admission._definition_resolver = AdmissionDefinitionResolver()
    scheduler.admission._objects = objects
    judge = JudgeService(
        uow,
        suites,
        RuleEvidenceReader(uow, content_factory=lambda _: None),
        scheduler.admission,
        execution_policy=scheduler.execution_policy,
    )
    run = await judge.schedule(scope, candidate, suite.rubric_version, "owned-context-judge")
    graph = build_context_runtime(
        uow_factory=uow, objects=objects, storage=storage, signing_secret="owned-context-judge"
    )
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=suites.budgets.inventory,
        physical_policy=physical,
        execution_policy=scheduler.execution_policy,
        model_validator=graph.model_validator,
        summary_validator=graph.summary_validator,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    models = InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None)
    monkeypatch.setattr(ApiKeyCipher, "decrypt_versioned", lambda *_: "fixture-credential")
    sends, clients = [], []

    async def native(request):
        wire = json.loads(request.content)
        assert not wire.get("tools")
        assert wire["messages"][0]["content"].startswith("Evaluation judge protocol v1.")
        assert "exact answer" in json.dumps(wire["messages"])
        assert "Evaluate the fixed authorized materials." not in json.dumps(wire["messages"])
        sends.append(wire)
        return httpx.Response(
            200,
            json={
                "id": "owned-judge",
                "created": 0,
                "object": "chat.completion",
                "model": wire["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "invalid judge output"},
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            },
        )

    def client(model, **kwargs):
        adapter = OpenAILLM(model)
        clients.append(adapter._client)
        adapter._client = AsyncOpenAI(
            base_url=model.base_url,
            api_key="fixture-credential",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(native)),
        )
        return adapter

    catalog = SimpleNamespace(
        definitions=AsyncMock(side_effect=AssertionError("judge tools requested"))
    )
    handlers = graph.handlers(
        definitions=definitions, tools=catalog, usage=usage, models=models, client_factory=client
    )
    materialization_errors = []
    materialize = handlers[0].preparation.materialize

    async def checked_materialize(*args, **kwargs):
        import traceback

        try:
            return await materialize(*args, **kwargs)
        except ValueError:
            materialization_errors.append(traceback.format_exc())
            raise

    handlers[0].preparation.materialize = checked_materialize
    registry = ActivityRegistry()
    for handler in (
        *handlers,
        ModelCallActivityHandler(
            objects=objects,
            models=models,
            tools=catalog,
            client_factory=client,
            execution_usage=usage,
            judge=JudgeRuntime(uow),
            context_validation=graph.prepared_validation,
        ),
    ):
        registry.register(handler)
    redis = Redis()
    runtime = build_execution_kernel_runtime(
        session_factory=sessions,
        redis=redis,
        authorization=auth,
        activity_registry=registry,
        definitions=definitions,
        worker_id="owned-judge-context",
        content_writer=ExecutionContentWriter(
            session_factory=sessions, authorization=auth, objects=objects
        ),
        evaluation_execution=EvaluationExecutionGuard(
            scheduler.execution_policy, session_factory=sessions, authorization=auth
        ),
    )
    try:
        assert (await runtime.run_inbox_once(limit=10)).accepted == 1
        for _ in range(5):
            await runtime.run_projector_once(scope, limit=100)
            decisions = await runtime.run_decisions_once(limit=1)
            assert not decisions.errors
            await runtime.run_activities_once(limit=1)
            async with uow(auth) as work:
                state = (
                    await PostgresVerifiedRunLoader(work.db_session).load(
                        run, owner_scope=(scope.user_id, None)
                    )
                ).replay.state
            if any(kind == "context.prepare" for _, kind, _ in state.requested_activities):
                break
        assert state.activity_results, (
            state.status,
            state.activity_failure_codes,
            state.settled_activities,
            materialization_errors,
        )
        assert not sends
        # The prepared body must inherit the judge's original source authority.
        async with uow(auth) as work:
            dependencies = await work.db_session.scalar(
                text(
                    "SELECT context_dependencies FROM execution_public_content WHERE run_id=:run AND phase='output' ORDER BY created_at DESC LIMIT 1"
                ),
                {"run": run},
            )
            assert any(item["kind"] == "judge" for item in dependencies)
            if revoke:
                await work.db_session.execute(
                    text("UPDATE users SET status='disabled' WHERE id=:id"), {"id": scope.user_id}
                )
                await work.commit()
        for _ in range(16):
            await runtime.run_projector_once(scope, limit=100)
            await runtime.run_decisions_once(limit=1)
            await runtime.run_activities_once(limit=1)
        assert len(sends) == (0 if revoke else 3)
        async with uow(auth) as work:
            state = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    run, owner_scope=(scope.user_id, None)
                )
            ).replay.state
            if not revoke:
                assert state.failure_code == "JUDGE_OUTPUT_INVALID"
                assert (
                    sum(kind == "context.prepare" for _, kind, _ in state.requested_activities) == 3
                )
                assert not any(
                    kind == "context.compact" for _, kind, _ in state.requested_activities
                )
    finally:
        await redis.aclose()
        for adapter_client in clients:
            await adapter_client.close()
