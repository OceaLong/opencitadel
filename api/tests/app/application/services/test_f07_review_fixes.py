from decimal import Decimal

import pytest

from app.domain.models.execution_usage import PriceSnapshot
from app.infrastructure.external.llm.base_llm import normalize_usage
from app.infrastructure.external.llm.dispatch import HTTPStream, SDKStream, StreamEvidence


@pytest.mark.parametrize("value", [-1, None, "invalid"])
def test_unknown_reasoning_with_differentiated_price_is_unknown(value):
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": value},
    }
    usage = normalize_usage(raw)
    assert (
        PriceSnapshot(
            input_per_million=Decimal(1),
            output_per_million=Decimal(2),
            reasoning_per_million=Decimal(10),
        ).cost(usage)
        is None
    )
    assert usage["category_states"]["reasoning_tokens"] in {"absent", "malformed"}


def test_cache_write_malformed_and_inapplicable_have_different_cost_coverage():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    price = PriceSnapshot(
        input_per_million=Decimal(1),
        output_per_million=Decimal(2),
        cache_write_per_million=Decimal(4),
    )
    assert price.cost(normalize_usage(raw)) == Decimal("0.00018")
    assert normalize_usage(raw)["category_states"]["cache_write_tokens"] == "inapplicable"
    assert price.cost(normalize_usage({**raw, "prompt_cache_miss_tokens": -1})) is None


class Guard:
    def __init__(self):
        self.facts = []

    async def after_send(self, *fact):
        self.facts.append(fact)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["eof", "error", "complete"])
async def test_anthropic_http_stream_requires_final_usage_and_message_stop(ending):
    import json

    class Response:
        async def aiter_lines(self):
            yield "data: " + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "model": "actual",
                        "usage": {"input_tokens": 100, "output_tokens": 1},
                    },
                }
            )
            if ending == "error":
                yield "data: " + json.dumps(
                    {"type": "error", "error": {"type": "overloaded_error"}}
                )
            if ending == "complete":
                yield "data: " + json.dumps(
                    {"type": "message_delta", "usage": {"output_tokens": 12}}
                )
                yield "data: " + json.dumps({"type": "message_stop"})

    guard = Guard()
    async for _ in HTTPStream(Response(), StreamEvidence(guard, "id", "anthropic")).aiter_lines():
        pass
    if ending == "complete":
        assert guard.facts[0][1]["completion_tokens"] == 12
    else:
        assert guard.facts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["eof", "error", "complete"])
async def test_openai_sdk_stream_requires_terminal_then_final_usage(ending):
    async def chunks():
        yield {
            "model": "actual",
            "choices": [{"finish_reason": None}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1},
        }
        if ending == "error":
            yield {"error": {"message": "remote failure"}}
        if ending == "complete":
            yield {"choices": [{"finish_reason": "stop"}]}
            yield {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 12}}

    guard = Guard()
    async for _ in SDKStream(chunks(), StreamEvidence(guard, "id", "openai")):
        pass
    if ending == "complete":
        assert guard.facts[0][1]["completion_tokens"] == 12
    else:
        assert guard.facts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rewrite", ["tools", "thinking"])
async def test_actual_openai_model_rewrite_cannot_reuse_original_price_or_pin(rewrite):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from openai.types.chat import ChatCompletion

    from app.application.ports.inference_dispatch import dispatch_context
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.infrastructure.external.llm.openai_llm import OpenAILLM
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    model = resolved_chat_model(
        model_name="deepseek-reasoner", extra_params={"thinking_model_name": "thinking-alias"}
    )
    model.model.input_price_per_million = 1
    model.model.output_price_per_million = 2
    requested = "deepseek-chat" if rewrite == "tools" else "thinking-alias"

    class Repo:
        async def snapshot(self, scope, run, body, purpose):
            self.body = body
            return "config"

        async def allocate(self, *args, **kwargs):
            return str(uuid4())

        async def record(self, scope, identity, fact):
            self.fact = fact
            return fact

    repo = Repo()

    @asynccontextmanager
    async def repositories():
        yield repo

    guard = ExecutionUsageService(repository_context=repositories).guard(
        scope=None,
        request=SimpleNamespace(activity_id=uuid4(), generation=0),
        context=SimpleNamespace(run=SimpleNamespace(run_id=uuid4()), claim_generation=1),
        purpose="production",
        resolved={"policy_revision": "p", "tool_fingerprint": "t"},
    )
    adapter = OpenAILLM(model, thinking_enabled=rewrite == "thinking")
    await adapter._client.close()

    async def create(**kwargs):
        assert kwargs["model"] == requested
        return ChatCompletion(
            id="local",
            created=0,
            model=requested,
            object="chat.completion",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        )

    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    with dispatch_context(guard, model):
        await adapter.invoke(
            [{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "read", "parameters": {}}}]
            if rewrite == "tools"
            else None,
        )
    assert repo.fact["cost_usd"] is None
    assert repo.fact["version_unpinned"] is True
    assert repo.body["requested_model"] == requested


@pytest.mark.asyncio
@pytest.mark.parametrize("identity_drift", ["price_only", "model_name", "provider", "endpoint"])
async def test_normal_agent_admission_captures_price_and_retry_keeps_original(identity_drift):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from uuid import uuid4

    from app.application.execution.admission import RunAdmissionService
    from app.application.services.agent_service import AgentService
    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.domain.models.scope import OwnerScope
    from app.domain.models.session import Session
    from tests.app.application.execution.test_policy_admission import (
        _active_execution,
        _Commands,
        _Objects,
        _PolicyHeads,
    )
    from tests.app.application.services.test_agent_service_admission import UnitOfWork
    from tests.app.application.services.test_agent_service_admission_postgres import (
        TerminalProjection,
    )
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    model = resolved_chat_model()
    model.model.input_price_per_million = 1
    model.model.output_price_per_million = 2

    class Repo:
        def __init__(self):
            self.anchor = None

        async def admission_snapshot(self, scope, run, body, purpose):
            if self.anchor is None:
                self.anchor = body
            return "original-admission"

        async def load_snapshot(self, *args):
            return self.anchor

        async def snapshot(self, scope, run, body, purpose):
            self.dispatched = body
            return "physical-config"

        async def allocate(self, *args, **kwargs):
            return str(uuid4())

    repo = Repo()

    @asynccontextmanager
    async def repositories():
        yield repo

    usage = ExecutionUsageService(repository_context=repositories)

    async def resolve_chat(model_id, scope, *, uow=None):
        return model

    resolver = usage.admission_resolver(SimpleNamespace(resolve_chat=resolve_chat))
    objects = _Objects()
    admission = RunAdmissionService(
        command_ingress=_Commands(),
        activity_objects=objects,
        policy_heads=_PolicyHeads(_active_execution()),
        configuration_resolver=resolver,
    )
    uow = UnitOfWork(Session(id="session-1", owner_user_id="user-1"), None)
    uow.execution_usage = repo
    agent = AgentService(
        uow_factory=lambda: uow,
        admission_service=admission,
        command_ingress=SimpleNamespace(),
        public_projection=TerminalProjection(),
        run_projection=SimpleNamespace(),
        poll_interval_seconds=0.001,
    )
    scope = OwnerScope.personal("user-1")
    request_id = uuid4()
    for rate in (1, 99):
        model.model.input_price_per_million = rate
        if rate == 99:
            if identity_drift == "model_name":
                model.model.model_name = "changed-wire-model"
            elif identity_drift == "provider":
                from app.domain.models.inference import InferenceProvider

                model.endpoint.provider = InferenceProvider.ANTHROPIC
            elif identity_drift == "endpoint":
                model.endpoint.id = str(uuid4())
        _ = [
            event
            async for event in agent.chat(
                "session-1", owner_scope=scope, message="hello", request_id=request_id
            )
        ]
    marker = objects.payloads[-1]["_execution_usage"]
    assert marker == {"purpose": "production", "configuration_id": "original-admission"}
    guard = usage.guard(
        scope=scope,
        request=SimpleNamespace(activity_id=uuid4(), generation=0),
        context=SimpleNamespace(run=SimpleNamespace(run_id=uuid4()), claim_generation=1),
        purpose="production",
        resolved={
            "policy_revision": "p",
            "tool_fingerprint": "t",
            "admission_configuration_id": marker["configuration_id"],
        },
    )
    await guard.before_send(model, {"model": model.model_name})
    assert repo.dispatched["model_id"] == repo.anchor["model_id"]
    assert repo.dispatched["requested_model"] == model.model_name
    assert repo.dispatched["provider"] == model.provider.value
    assert repo.dispatched["endpoint_id"] == model.endpoint.id
    expected_rate = 1 if identity_drift == "price_only" else 99
    assert Decimal(repo.dispatched["price"]["input_per_million"]) == expected_rate
    assert (repo.dispatched["price_revision"] == repo.anchor["price_revision"]) is (
        identity_drift == "price_only"
    )
    assert repo.dispatched["price"]["provenance"] == "legacy_positive"


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["eof", "error", "complete", "exception"])
async def test_gemini_http_requires_final_chunk_usage_and_no_stream_failure(ending):
    import json

    class Response:
        async def aiter_lines(self):
            yield "data: " + json.dumps(
                {
                    "modelVersion": "actual",
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 1},
                    "candidates": [{"content": {}}],
                }
            )
            if ending == "error":
                yield "data: " + json.dumps({"error": {"message": "failure"}})
            if ending in ("complete", "exception"):
                yield "data: " + json.dumps(
                    {
                        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 12},
                        "candidates": [{"finishReason": "STOP"}],
                    }
                )
            if ending == "exception":
                raise ConnectionError("controlled EOF error")

    guard = Guard()

    async def consume():
        async for _ in HTTPStream(Response(), StreamEvidence(guard, "id", "gemini")).aiter_lines():
            pass

    if ending == "exception":
        with pytest.raises(ConnectionError):
            await consume()
    else:
        await consume()
    if ending == "complete":
        assert guard.facts[0][1]["completion_tokens"] == 12
    else:
        assert guard.facts == []


def test_explicit_shared_reasoning_rate_covers_absent_split_only_when_declared():
    raw = {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 0},
    }
    usage = normalize_usage(raw)
    rates = {"input_per_million": Decimal(1), "output_per_million": Decimal(2)}
    assert PriceSnapshot(**rates).cost(usage) is None
    assert PriceSnapshot(**rates, reasoning_uses_output_rate=True).cost(usage) == Decimal("0.00018")
