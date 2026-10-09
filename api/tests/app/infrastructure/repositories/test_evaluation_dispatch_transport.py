# ruff: noqa: F401,F811
"""D2 actual adapters compose one committed F07/budget physical receipt."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from tests.app.infrastructure.repositories.test_evaluation_physical_dispatch import (
    budget_binding_fixture,
    configurations,
    datasets,
    fresh_f07_database,
    isolated_database,
    ready_dispatch,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


def _fake_openai_client(create):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
        close=AsyncMock(),
    )


async def test_usage_guard_delegates_one_atomic_receipt_before_native_openai_transport(
    ready_dispatch,
):
    from openai.types.chat import ChatCompletion

    from app.application.ports.inference_dispatch import dispatch_context
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    fixture, binding, request, context, model, payload, physical, execution, _ = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )

    @asynccontextmanager
    async def forbidden_repository():
        raise AssertionError("old F07 guard must not independently allocate")
        yield

    usage = ExecutionUsageService(
        repository_context=forbidden_repository, physical_dispatch=durable
    )
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="evaluation_subject",
        resolved={
            "policy_revision": str(context.run.policy_snapshot.execution_revision_id),
            "tool_fingerprint": "none",
        },
    )
    sends = []

    async def create(**wire):
        assert wire["max_completion_tokens"] == 8192
        assert wire.get("n", 1) == 1
        async with uow(durable.authorization) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                    {"run": binding.run_id},
                )
                == 1
            )
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM evaluation_budget_reservations")
                )
                == 1
            )
        sends.append(wire)
        return ChatCompletion(
            id="fake",
            created=0,
            object="chat.completion",
            model=model.model_name,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    adapter = OpenAILLM(model)
    original = adapter._client
    adapter._client = _fake_openai_client(create)
    try:
        with dispatch_context(guard, model):
            assert (await adapter.invoke(payload["messages"]))["content"] == "ok"
    finally:
        await original.close()
    assert len(sends) == 1
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 1
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )


async def test_actual_model_handler_installs_bound_candidates_and_payload_guard(ready_dispatch):
    from openai.types.chat import ChatCompletion

    from app.application.evaluation.budget_candidates import BudgetPayloadGuard
    from app.application.execution.activities.model_call import ModelCallActivityHandler
    from app.application.ports.inference_dispatch import (
        current_candidate_authority,
        current_dispatch,
    )
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    fixture, _, request, context, model, _, physical, execution, _ = ready_dispatch
    service, _, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)

    class Objects:
        async def load_input(self, **kwargs):
            # Purpose hint is not trusted: durable C1 binding remains authoritative.
            return {
                "message": "hello",
                "model_id": model.id,
                "_execution_usage": {"purpose": "production"},
            }

        async def put_result(self, *args):
            return "fake-result"

    class CheckedAdapter(OpenAILLM):
        async def invoke(self, *args, **kwargs):
            assert current_candidate_authority.get() is not None
            assert isinstance(current_dispatch.get()[0], BudgetPayloadGuard)
            return await super().invoke(*args, **kwargs)

    clients = []

    async def create(**wire):
        return ChatCompletion(
            id="fake",
            created=0,
            object="chat.completion",
            model=model.model_name,
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    def factory(resolved, **kwargs):
        client = CheckedAdapter(resolved)
        clients.append(client._client)
        client._client = _fake_openai_client(create)
        return client

    handler = ModelCallActivityHandler(
        objects=Objects(),
        models=InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None),
        tools=None,
        execution_usage=usage,
        client_factory=factory,
    )
    try:
        outcome = await handler.execute(request, context)
        assert outcome.status == "succeeded"
    finally:
        for client in clients:
            await client.close()
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT demand->>'purpose' FROM evaluation_budget_reservations")
            )
            == "evaluation_subject"
        )


@pytest.mark.parametrize("ending", ["complete", "partial", "cancel", "early_eof"])
async def test_native_sdk_stream_finalizes_once_only_after_completion(ready_dispatch, ending):
    import asyncio

    from openai.types.chat import ChatCompletionChunk

    from app.application.ports.inference_dispatch import dispatch_context
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    fixture, _, request, context, model, _, physical, execution, _ = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="evaluation_subject",
        resolved={"policy_revision": "test", "tool_fingerprint": "none"},
    )

    def chunk(**kwargs):
        return ChatCompletionChunk(
            id="fake", created=0, object="chat.completion.chunk", model=model.model_name, **kwargs
        )

    async def chunks():
        yield chunk(
            choices=[{"index": 0, "finish_reason": None, "delta": {"content": "hello"}}],
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )
        async with uow(durable.authorization) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_settlements")
                )
                == 0
            )
        if ending == "cancel":
            raise asyncio.CancelledError()
        if ending == "early_eof":
            return
        yield chunk(choices=[{"index": 0, "finish_reason": "stop", "delta": {}}])
        if ending == "complete":
            yield chunk(
                choices=[], usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}
            )

    async def create(**kwargs):
        return chunks()

    adapter = OpenAILLM(model)
    original = adapter._client
    adapter._client = _fake_openai_client(create)
    try:
        with dispatch_context(guard, model):
            if ending == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    async for _ in adapter.stream_invoke([{"role": "user", "content": "hello"}]):
                        pass
            else:
                async for _ in adapter.stream_invoke([{"role": "user", "content": "hello"}]):
                    pass
    finally:
        await original.close()
    async with uow(durable.authorization) as work:
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens,spent_tokens FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["slots"] == (0 if ending in ("complete", "partial") else 1)
        assert row["spent_tokens"] == (5 if ending == "complete" else 0)
        assert (row["reserved_tokens"] == 0) == (ending == "complete")
        assert await work.db_session.scalar(
            text("SELECT count(*) FROM execution_model_settlements")
        ) == (1 if ending in ("complete", "partial") else 0)


async def test_new_admission_borrows_uow_and_captures_verified_original_requester(ready_dispatch):
    from uuid import uuid4

    from app.application.security.authorization_context import authorization_scope
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService

    fixture, _, _, _, model, _, physical, execution, _ = ready_dispatch
    service, scope, principal, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )

    @asynccontextmanager
    async def forbidden_repository():
        raise AssertionError("admission must borrow its caller UoW")
        yield

    usage = ExecutionUsageService(
        repository_context=forbidden_repository, physical_dispatch=durable
    )
    models = InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None)
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    with authorization_scope(auth):
        async with uow(auth) as work:
            identity = await usage.admission_resolver(models)(
                scope,
                uuid4(),
                "policy",
                {"message": "hello", "model_id": model.id},
                "production",
                inference_read_context=work,
            )
            body = await work.db_session.scalar(
                text("SELECT body FROM execution_configurations WHERE id=:id"), {"id": identity}
            )
            assert body["physical_requester"]["proof"]["principal"] == principal.model_dump(
                mode="json"
            )
            assert body["physical_requester"]["proof"]["kind"] == "user"
            assert len(body["physical_requester"]["signature"]) == 64
            await work.commit()


async def ordinary_activity(
    ready,
    *,
    capture=True,
    historical=False,
    system=False,
    activity_type="model.call",
    activity_payload=None,
    admission_payload=None,
    policy_snapshot=None,
    source_entity_type="session",
):
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from app.application.execution.decisions.base import activity_identity
    from app.application.execution.run_context import run_execution_context
    from app.application.security.authorization_context import authorization_scope
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.domain.execution.activity import ActivityContext
    from app.domain.execution.run import RunState
    from app.domain.models.authorization import AuthorizationContext
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from tests.app.infrastructure.repositories.test_evaluation_execution_slots import run_command

    fixture, original, _, old_context, model, _, physical, execution, handler = ready
    service, scope, principal, _, _, _, uow = fixture
    binding = original.model_copy(
        update={
            "run_id": uuid4(),
            "source_entity_id": str(uuid4()),
            "source_entity_type": source_entity_type,
        }
    )
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    from app.domain.models.scope import OwnerScope

    def command(kind, payload=None):
        command_fixture = list(fixture)
        command_fixture[1] = OwnerScope.personal(scope.user_id)
        value = run_command(
            command_fixture,
            binding,
            policy_snapshot or old_context.run.policy_snapshot,
            kind,
            payload,
        )
        return (
            value.model_copy(update={"owner_user_id": None, "team_id": scope.team_id})
            if scope.team_id
            else value
        )

    if historical:
        from tests.app.execution_test_support import execution_admin_session

        async with execution_admin_session() as db:
            await db.execute(
                text(
                    "INSERT INTO execution_stream_owners(stream_type,stream_id,owner_user_id,team_id,created_at) VALUES('run',:run,:owner,:team,clock_timestamp()-interval '1 day')"
                ),
                {
                    "run": str(binding.run_id),
                    "owner": None if scope.team_id else scope.user_id,
                    "team": scope.team_id,
                },
            )
            await db.commit()
    config_id = None
    if capture:
        auth = (
            AuthorizationContext.system("trusted-patrol")
            if system
            else AuthorizationContext.for_principal(principal, scope=scope)
        )
        with authorization_scope(auth):
            async with uow(auth) as work:
                config_id = await usage.admission_resolver(
                    InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None)
                )(
                    scope,
                    binding.run_id,
                    "policy",
                    admission_payload
                    if admission_payload is not None
                    else {"message": "hello", "model_id": model.id},
                    "production",
                    inference_read_context=work,
                )
                await work.commit()
    for kind in ("CreateRun", "StartRun"):
        assert (await handler.handle(command(kind))).status == "accepted"
    async with uow(durable.authorization) as work:
        from app.domain.execution.aggregate import replay
        from app.domain.execution.run import RunAggregate
        from app.infrastructure.execution.postgres_event_store import PostgresEventStore

        aggregate = RunAggregate()
        events = await PostgresEventStore(
            work.db_session, event_registries={"run": aggregate.event_registry}
        ).load_stream("run", str(binding.run_id))
        state = replay(aggregate, events, stream_id=str(binding.run_id)).state
    activity_id = activity_identity(state, "model:0")
    assert (
        await handler.handle(
            command(
                "RequestActivity",
                {
                    "activity_id": str(activity_id),
                    "activity_type": activity_type,
                    "input_payload": activity_payload or {},
                    "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                    "input_ref": "test",
                    "input_digest": "test",
                },
            )
        )
    ).status == "accepted"
    store = PostgresActivityStore(
        session_factory=handler._session_factory, authorization=durable.authorization
    )
    claim = next(
        c
        for c in await store.claim(
            now=datetime.now(UTC), limit=10, worker_id="ordinary", claim_ttl=timedelta(minutes=5)
        )
        if c.request.activity_id == activity_id
    )
    assert await store.mark_call_started(claim, now=datetime.now(UTC))
    assert (
        await handler.handle(
            command(
                "MarkActivityCallStarted",
                {
                    "activity_id": str(activity_id),
                    "generation": 0,
                    "claim_generation": claim.claim_generation,
                },
            ).model_copy(update={"command_schema_version": 2})
        )
    ).status == "accepted"
    context = ActivityContext(
        worker_id="ordinary",
        claim_generation=claim.claim_generation,
        idempotency_key=str(activity_id),
        owner_user_id=None if scope.team_id else scope.user_id,
        team_id=scope.team_id,
        run=run_execution_context(state),
    )
    return durable, usage, binding, claim.request, context, config_id


async def test_ordinary_model_calls_share_counted_capacity_without_evaluation_retry_cap(
    ready_dispatch,
):
    from app.infrastructure.external.llm.base_llm import normalize_usage

    fixture, _, _, _, model, payload, _, _, _ = ready_dispatch
    _, scope, _, _, _, _, uow = fixture
    durable, usage, binding, request, context, config_id = await ordinary_activity(ready_dispatch)
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="production",
        resolved={
            "policy_revision": "policy",
            "tool_fingerprint": "none",
            "admission_configuration_id": config_id,
        },
    )
    for _ in range(4):
        identity = (await guard.before_send(model, payload)).consume()
        async with uow(durable.authorization) as work:
            assert (
                await work.db_session.scalar(
                    text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                )
                == 1
            )
        await guard.after_send(
            identity,
            normalize_usage({"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}),
            model.model_name,
        )
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 4
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_model_logical_calls WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 0
        )


async def team_ready(ready_dispatch):
    from uuid import uuid4

    from app.domain.models.scope import OwnerScope
    from app.domain.models.team import TeamRole
    from tests.app.execution_test_support import execution_admin_session

    fixture = ready_dispatch[0]
    principal = fixture[2]
    team = str(uuid4())
    async with execution_admin_session() as db:
        await db.execute(text("INSERT INTO teams(id,name) VALUES(:id,:id)"), {"id": team})
        await db.execute(
            text("INSERT INTO team_members(team_id,user_id,role) VALUES(:team,:user,'member')"),
            {"team": team, "user": principal.user_id},
        )
        await db.commit()
    changed = list(fixture)
    changed[1] = OwnerScope.team(principal.user_id, team)
    changed[2] = principal.model_copy(update={"team_roles": {team: TeamRole.MEMBER}})
    return (tuple(changed), *ready_dispatch[1:])


@pytest.mark.parametrize("historical", [False, True])
async def test_team_provenance_uses_original_user_or_explicit_legacy_aggregate(
    ready_dispatch, historical
):
    ready = await team_ready(ready_dispatch)
    fixture = ready[0]
    _, scope, principal, _, _, _, uow = fixture
    durable, usage, _, request, context, config_id = await ordinary_activity(
        ready, capture=not historical, historical=historical
    )
    guard = usage.guard(
        scope=context.run.owner_scope,
        request=request,
        context=context,
        purpose="production",
        resolved={
            "policy_revision": "policy",
            "tool_fingerprint": "none",
            "admission_configuration_id": config_id,
        },
    )
    (await guard.before_send(ready[4], ready[5])).consume()
    async with uow(durable.authorization) as work:
        demand = await work.db_session.scalar(
            text("SELECT demand FROM evaluation_budget_reservations")
        )
        assert demand["requester"] == (
            "legacy_unknown_requester" if historical else principal.user_id
        )
        assert demand["scope"] == "team:" + scope.team_id


async def test_new_missing_requester_proof_cannot_use_legacy_fallback(ready_dispatch):
    ready = await team_ready(ready_dispatch)
    _, usage, _, request, context, _ = await ordinary_activity(ready, capture=False)
    guard = usage.guard(
        scope=context.run.owner_scope,
        request=request,
        context=context,
        purpose="production",
        resolved={"policy_revision": "policy", "tool_fingerprint": "none"},
    )
    with pytest.raises(ValueError, match="physical_requester_proof_required"):
        await guard.before_send(ready[4], ready[5])


async def test_original_team_requester_revocation_denies_new_send_but_preserves_late_completion(
    ready_dispatch,
):
    from tests.app.execution_test_support import execution_admin_session

    ready = await team_ready(ready_dispatch)
    fixture = ready[0]
    _, scope, principal, _, _, _, uow = fixture
    durable, usage, _, request, context, config_id = await ordinary_activity(ready)
    guard = usage.guard(
        scope=context.run.owner_scope,
        request=request,
        context=context,
        purpose="production",
        resolved={
            "policy_revision": "policy",
            "tool_fingerprint": "none",
            "admission_configuration_id": config_id,
        },
    )
    identity = (await guard.before_send(ready[4], ready[5])).consume()
    async with execution_admin_session() as db:
        await db.execute(
            text("DELETE FROM team_members WHERE team_id=:team AND user_id=:user"),
            {"team": scope.team_id, "user": principal.user_id},
        )
        await db.commit()
    with pytest.raises(PermissionError, match="membership revoked"):
        await guard.before_send(ready[4], ready[5])
    await guard.after_completion(identity, ready[4].model_name)
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 1
        )


async def test_system_admission_keeps_explicit_actor_provenance(ready_dispatch):
    ready = await team_ready(ready_dispatch)
    durable, usage, _, request, context, config_id = await ordinary_activity(ready, system=True)
    guard = usage.guard(
        scope=context.run.owner_scope,
        request=request,
        context=context,
        purpose="production",
        resolved={
            "policy_revision": "policy",
            "tool_fingerprint": "none",
            "admission_configuration_id": config_id,
        },
    )
    (await guard.before_send(ready[4], ready[5])).consume()
    async with ready[0][-1](durable.authorization) as work:
        requester = await work.db_session.scalar(
            text("SELECT requester FROM evaluation_budget_reservations")
        )
        assert requester.startswith("system:sha256:")
        assert requester != ready[0][2].user_id


@pytest.mark.parametrize("status", [429, 500, 408])
async def test_native_sdk_rejection_and_uncertain_errors_have_distinct_completion(
    ready_dispatch, status
):
    import httpx
    from openai import APIStatusError

    from app.application.ports.inference_dispatch import dispatch_context
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.dispatch import physical_send

    fixture, _, request, context, model, payload, physical, execution, _ = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="evaluation_subject",
        resolved={"policy_revision": "policy", "tool_fingerprint": "none"},
    )

    async def rejected():
        raise APIStatusError(
            "fake rejection",
            response=httpx.Response(
                status, request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
            ),
            body={},
        )

    with dispatch_context(guard, model), pytest.raises(APIStatusError):
        await physical_send(rejected, payload, provider="openai")
    async with uow(durable.authorization) as work:
        row = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,reserved_tokens FROM evaluation_budget_buckets WHERE key='0:global'"
                    )
                )
            )
            .mappings()
            .one()
        )
        assert row["slots"] == (0 if status == 429 else 1)
        assert row["reserved_tokens"] > 1000000
        assert await work.db_session.scalar(
            text("SELECT count(*) FROM execution_model_settlements")
        ) == (1 if status == 429 else 0)


async def test_forged_or_other_run_private_proof_cannot_authorize_actual_send(ready_dispatch):
    from copy import deepcopy
    from uuid import uuid4

    from app.infrastructure.repositories.db_physical_requester_repository import (
        DBPhysicalRequesterRepository,
    )

    fixture = ready_dispatch[0]
    _, scope, principal, _, _, _, uow = fixture
    durable, usage, binding, request, context, _ = await ordinary_activity(
        ready_dispatch, capture=False
    )
    async with uow(durable.authorization) as work:
        repo = DBPhysicalRequesterRepository(
            work.db_session, signing_secret=work.evaluation_budget.secret
        )
        # Even a genuine envelope is not transferable to another Run.
        sealed = repo._seal(
            {
                "version": 1,
                "kind": "user",
                "scope": "user:" + scope.user_id,
                "run_id": str(uuid4()),
                "principal": principal.model_dump(mode="json"),
            }
        )
        with pytest.raises(ValueError, match="physical_requester_proof_invalid"):
            repo.verify(sealed, scope=scope, run_id=binding.run_id)
        forged = deepcopy(sealed)
        forged["proof"]["run_id"] = str(binding.run_id)
        await work.execution_usage.admission_snapshot(
            scope,
            binding.run_id,
            {"stage": "admission", "physical_requester": forged},
            "production",
        )
        await work.commit()
    guard = usage.guard(
        scope=scope, request=request, context=context, purpose="production", resolved={}
    )
    with pytest.raises(ValueError, match="physical_requester_proof_invalid"):
        await guard.before_send(ready_dispatch[4], ready_dispatch[5])
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 0
        )


async def test_capture_rejects_caller_authorization_not_matching_signed_database_claims(
    ready_dispatch,
):
    from uuid import uuid4

    from app.domain.models.authorization import AuthorizationContext

    _, scope, principal, _, _, _, uow = ready_dispatch[0]
    async with uow(AuthorizationContext.system("execution-kernel")) as work:
        with pytest.raises(ValueError, match="physical_requester_authority_invalid"):
            await work.execution_usage.capture_requester(
                scope, AuthorizationContext.for_principal(principal, scope=scope), run_id=uuid4()
            )


async def test_registered_evaluation_and_unmapped_ordinary_aliases_share_provider_kind_cap(
    ready_dispatch,
):
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.domain.evaluation.budget import BudgetPolicy
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService

    fixture, _, request, context, model, payload, _, execution, _ = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    ordinary, usage, _, orequest, ocontext, _ = await ordinary_activity(ready_dispatch)
    policy = BudgetPolicy(
        revision=2, global_concurrency=10, user_concurrency=10, provider_concurrency=1
    )
    async with uow(ordinary.authorization) as work:
        await work.evaluation_physical_policy.activate(policy, expected_revision=1)
        await work.commit()
    ordinary.physical_policy = policy
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=policy,
        execution_policy=execution,
    )
    usage_eval = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    eguard = usage_eval.guard(
        scope=scope, request=request, context=context, purpose="evaluation_subject", resolved={}
    )
    identity = (await eguard.before_send(model, payload)).consume()
    alias = model.model_copy(deep=True)
    alias.endpoint.id = "unregistered-alias"
    oguard = usage.guard(
        scope=scope,
        request=orequest,
        context=ocontext,
        purpose="production",
        resolved={"policy_revision": "policy", "tool_fingerprint": "none"},
    )
    with pytest.raises(ValueError, match="budget_concurrency_exhausted"):
        await oguard.before_send(alias, payload)
    await eguard.after_completion(identity, None)
    (await oguard.before_send(alias, payload)).consume()
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT slots FROM evaluation_budget_buckets WHERE key='1:provider:kind:openai'"
                )
            )
            == 1
        )
        assert (
            await work.db_session.scalar(
                text(
                    "SELECT slots FROM evaluation_budget_buckets WHERE key='1:provider:account:unknown'"
                )
            )
            == 1
        )
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_dispatches"))
            == 2
        )
        assert (
            await work.db_session.scalar(
                text("SELECT reserved_tokens FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            > 0
        )


@pytest.mark.parametrize("budget_binding_fixture", [{"token_budget": 5000000}], indirect=True)
@pytest.mark.parametrize("mode", ["quota_fallback", "uncertain_retry_cap"])
async def test_real_resilience_and_sdk_sends_share_frozen_authority_and_logical_cap(
    ready_dispatch, mode
):
    import httpx
    from openai import APIStatusError
    from openai.types.chat import ChatCompletion

    from app.application.evaluation.budget_candidates import BudgetPayloadGuard
    from app.application.ports.inference_dispatch import (
        candidate_authority_context,
        dispatch_context,
    )
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from app.infrastructure.external.llm.openai_llm import OpenAILLM
    from app.infrastructure.external.llm.resilient_llm import ModelUnavailableError
    from tests.app.infrastructure.external.llm.test_resilient_llm import ResilientLLMClient, _policy

    fixture, _, request, context, model, payload, physical, execution, _ = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    frozen = await durable.candidates(scope, context)
    primary = frozen.primary(model)
    guard = BudgetPayloadGuard(
        frozen,
        usage.guard(
            scope=scope, request=request, context=context, purpose="evaluation_subject", resolved={}
        ),
    )
    sent = []
    clients = []

    def create_model_client(candidate, **kwargs):
        adapter = OpenAILLM(candidate)
        assert adapter._client.max_retries == 0
        clients.append(adapter._client)

        async def create(**wire):
            sent.append(candidate.id)
            async with uow(durable.authorization) as work:
                assert await work.db_session.scalar(
                    text("SELECT count(*) FROM execution_model_dispatches")
                ) == len(sent)
                assert await work.db_session.scalar(
                    text("SELECT count(*) FROM evaluation_budget_reservations")
                ) == len(sent)
                if len(sent) == 2 and mode == "quota_fallback":
                    assert (
                        await work.db_session.scalar(
                            text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                        )
                        == 1
                    )
                    assert (
                        await work.db_session.scalar(
                            text(
                                "SELECT reserved_tokens FROM evaluation_budget_buckets WHERE key='0:global'"
                            )
                        )
                        > 2000000
                    )
            if mode == "uncertain_retry_cap" or candidate.id == primary.id:
                status = 500 if mode == "uncertain_retry_cap" else 429
                raise APIStatusError(
                    "Error code: 500 - server error" if status == 500 else "insufficient_quota",
                    response=httpx.Response(
                        status,
                        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
                    ),
                    body={
                        "error": {"code": "insufficient_quota" if status == 429 else "server_error"}
                    },
                )
            return ChatCompletion(
                id="fake",
                created=0,
                object="chat.completion",
                model=candidate.model_name,
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "fallback"},
                    }
                ],
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )

        adapter._client = _fake_openai_client(create)
        return adapter

    client = ResilientLLMClient(
        create_model_client(primary),
        primary,
        policy=_policy(max_attempts_per_call=4),
        inference_model_service=InferenceModelService(
            uow, InfrastructureInferenceProviderAdapter(), None, None
        ),
        scope=scope,
        model_client_factory=SimpleNamespace(create_model_client=create_model_client),
    )
    try:
        with candidate_authority_context(frozen), dispatch_context(guard, primary):
            if mode == "quota_fallback":
                assert (await client.invoke(payload["messages"]))["content"] == "fallback"
                assert sent == [primary.id, "e05-fallback"]
            else:
                with pytest.raises(
                    ModelUnavailableError, match="budget_logical_attempts_exhausted"
                ):
                    await client.invoke(payload["messages"])
                assert sent == [primary.id] * 3
    finally:
        for original in clients:
            await original.close()
    async with uow(durable.authorization) as work:
        assert await work.db_session.scalar(
            text("SELECT count(*) FROM execution_model_settlements")
        ) == (2 if mode == "quota_fallback" else 0)
        assert await work.db_session.scalar(
            text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
        ) == (0 if mode == "quota_fallback" else 3)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_ordinary_adapter_hidden_resends_each_commit_new_physical_receipt(
    ready_dispatch, provider, streaming
):
    import httpx
    from openai import APIStatusError
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    from app.application.ports.inference_dispatch import dispatch_context
    from app.domain.models.inference import InferenceProvider
    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    fixture = ready_dispatch[0]
    _, scope, _, _, _, _, uow = fixture
    durable, usage, _, request, context, _ = await ordinary_activity(ready_dispatch)
    model = ready_dispatch[4].model_copy(deep=True)
    if provider == "anthropic":
        model.endpoint.provider = InferenceProvider.ANTHROPIC
        model.endpoint.base_url = "https://api.anthropic.com"
        model.model.model_name = "claude-haiku-4-5-20251001"
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="production",
        resolved={"policy_revision": "policy", "tool_fingerprint": "none"},
    )
    sent = []

    async def record(wire):
        sent.append(wire)
        async with uow(durable.authorization) as work:
            assert await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches")
            ) == len(sent)
            assert await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            ) == len(sent)
            assert (
                await work.db_session.scalar(
                    text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
                )
                == 1
            )

    messages = (
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}},
                ],
            }
        ]
        if provider == "openai"
        else [{"role": "system", "content": "cached"}, {"role": "user", "content": "hello"}]
    )
    adapter = OpenAILLM(model) if provider == "openai" else AnthropicLLM(model)
    original = adapter._client
    if provider == "openai":

        async def chunks():
            yield ChatCompletionChunk(
                id="fake",
                created=0,
                object="chat.completion.chunk",
                model=model.model_name,
                choices=[{"index": 0, "finish_reason": "stop", "delta": {"content": "ok"}}],
            )
            yield ChatCompletionChunk(
                id="fake",
                created=0,
                object="chat.completion.chunk",
                model=model.model_name,
                choices=[],
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )

        async def create(**wire):
            await record(wire)
            if len(sent) == 1:
                raise APIStatusError(
                    "invalid image",
                    response=httpx.Response(
                        400,
                        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
                    ),
                    body={},
                )
            assert wire["messages"][0]["content"] == "hello"
            if streaming:
                return chunks()
            return ChatCompletion(
                id="fake",
                created=0,
                object="chat.completion",
                model=model.model_name,
                choices=[
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )

        adapter._client = _fake_openai_client(create)
    else:
        import json

        async def transport(req):
            await record(json.loads(req.content))
            if len(sent) == 1:
                return httpx.Response(400, json={"error": {"message": "cache unsupported"}})
            assert sent[1]["system"] == "cached"
            if streaming:
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "model": model.model_name,
                            "usage": {
                                "input_tokens": 2,
                                "cache_creation_input_tokens": 0,
                                "cache_read_input_tokens": 0,
                            },
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "ok"},
                    },
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 1},
                    },
                    {"type": "message_stop"},
                ]
                return httpx.Response(
                    200,
                    content="".join(
                        "data: " + json.dumps(event) + "\n\n" for event in events
                    ).encode(),
                )
            return httpx.Response(
                200,
                json={
                    "model": model.model_name,
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            )

        adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        with dispatch_context(guard, model):
            if streaming:
                async for _ in adapter.stream_invoke(messages):
                    pass
            else:
                await adapter.invoke(messages)
    finally:
        if provider == "openai":
            await original.close()
        else:
            await original.aclose()
            await adapter._client.aclose()
    assert len(sent) == 2
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(text("SELECT count(*) FROM execution_model_settlements"))
            == 2
        )
        assert (
            await work.db_session.scalar(
                text("SELECT slots FROM evaluation_budget_buckets WHERE key='0:global'")
            )
            == 0
        )


@pytest.mark.parametrize("drift", ["final_payload", "candidate_metadata", "handler_override"])
async def test_actual_evaluation_dispatch_rejects_drift_before_transport(ready_dispatch, drift):
    from app.application.execution.activities.model_call import ModelCallActivityHandler
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.application.services.inference_model_service import InferenceModelService
    from app.infrastructure.adapters.inference_ports import InfrastructureInferenceProviderAdapter
    from app.infrastructure.execution.budget_dispatch import DurableBudgetDispatchService
    from tests.app.execution_test_support import execution_admin_session

    fixture, binding, request, context, model, payload, physical, execution, _ = ready_dispatch
    service, scope, _, _, _, _, uow = fixture
    durable = DurableBudgetDispatchService(
        uow_factory=uow,
        inventory=service.budgets.inventory,
        physical_policy=physical,
        execution_policy=execution,
    )
    usage = ExecutionUsageService(repository_context=None, physical_dispatch=durable)
    if drift == "handler_override":

        class Objects:
            async def load_input(self, **kwargs):
                return {"message": "hello", "model_id": model.id, "temperature_override": 1.98}

        def factory(*args, **kwargs):
            raise AssertionError("drift must fail before client creation")

        handler = ModelCallActivityHandler(
            objects=Objects(),
            models=InferenceModelService(uow, InfrastructureInferenceProviderAdapter(), None, None),
            tools=None,
            execution_usage=usage,
            client_factory=factory,
        )
        with pytest.raises(ValueError, match="budget_candidate_changed"):
            await handler.execute(request, context)
    else:
        if drift == "candidate_metadata":
            async with execution_admin_session() as db:
                await db.execute(
                    text(
                        "UPDATE inference_models SET settings=jsonb_set(settings,'{temperature}','1.98') WHERE id='e05-fallback'"
                    )
                )
                await db.commit()
        guard = usage.guard(
            scope=scope, request=request, context=context, purpose="evaluation_subject", resolved={}
        )
        wire = {**payload, "n": 2} if drift == "final_payload" else payload
        with pytest.raises(
            ValueError, match=r"budget_payload_bound_mismatch|budget_candidate_changed"
        ):
            await guard.before_send(model, wire)
    async with uow(durable.authorization) as work:
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM execution_model_dispatches WHERE run_id=:run"),
                {"run": binding.run_id},
            )
            == 0
        )
        assert (
            await work.db_session.scalar(
                text("SELECT count(*) FROM evaluation_budget_reservations")
            )
            == 0
        )


async def test_private_requester_envelope_never_enters_public_usage_projection(ready_dispatch):
    import json

    from app.infrastructure.execution.postgres_execution_usage import ExecutionUsageMaintenance
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector

    fixture = ready_dispatch[0]
    _, scope, _, _, _, _, uow = fixture
    durable, usage, _, request, context, config_id = await ordinary_activity(ready_dispatch)
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="production",
        resolved={"policy_revision": "policy", "tool_fingerprint": "none"},
    )
    identity = (await guard.before_send(ready_dispatch[4], ready_dispatch[5])).consume()
    await guard.after_completion(identity, None)
    handler = ready_dispatch[-1]
    maintenance = ExecutionUsageMaintenance(
        session_factory=handler._session_factory,
        authorization=durable.authorization,
        handler=handler,
    )
    await maintenance.process_pending()
    await maintenance.process_pending()
    projector = PostgresFormalProjector(
        session_factory=handler._session_factory, authorization=durable.authorization
    )
    await projector.run_once(scope, limit=1000)
    async with uow(durable.authorization) as work:
        proof = await work.db_session.scalar(
            text("SELECT body->'physical_requester' FROM execution_configurations WHERE id=:id"),
            {"id": config_id},
        )
        public = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT o.public_payload FROM execution_usage_publications p JOIN execution_view_observations o ON o.event_id=p.event_id WHERE p.call_identity=:id"
                    ),
                    {"id": identity},
                )
            )
            .scalars()
            .all()
        )
        assert len(public) == 2
        serialized = json.dumps(public)
        assert "physical_requester" not in serialized
        assert proof["signature"] not in serialized
        assert ready_dispatch[4].credential not in serialized


async def test_actual_ordinary_guard_counts_occupancy_under_null_physical_caps(ready_dispatch):
    from app.domain.evaluation.budget import BudgetPolicy

    durable, usage, _, request, context, _ = await ordinary_activity(ready_dispatch)
    scope = ready_dispatch[0][1]
    uow = ready_dispatch[0][-1]
    async with uow(durable.authorization) as work:
        await work.evaluation_physical_policy.activate(
            BudgetPolicy(revision=2), expected_revision=1
        )
        await work.commit()
    durable.physical_policy = BudgetPolicy(revision=2)
    guard = usage.guard(
        scope=scope,
        request=request,
        context=context,
        purpose="production",
        resolved={"policy_revision": "policy", "tool_fingerprint": "none"},
    )
    (await guard.before_send(ready_dispatch[4], ready_dispatch[5])).consume()
    async with uow(durable.authorization) as work:
        rows = (
            (
                await work.db_session.execute(
                    text(
                        "SELECT slots,limits FROM evaluation_budget_buckets WHERE key LIKE '0:%' OR key LIKE '1:%' OR key LIKE '2:%'"
                    )
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 4
        assert all(row["slots"] == 1 and row["limits"].get("slots") is None for row in rows)
