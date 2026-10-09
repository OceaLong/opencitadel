from decimal import Decimal

import pytest

from app.infrastructure.external.llm.base_llm import normalize_usage


def test_anthropic_inclusive_input():
    usage = normalize_usage(
        {
            "input_tokens": 20,
            "cache_creation_input_tokens": 30,
            "cache_read_input_tokens": 50,
            "output_tokens": 10,
        }
    )
    assert usage["prompt_tokens"] == 100


def test_gemini_thoughts_are_additional():
    usage = normalize_usage(
        {
            "promptTokenCount": 100,
            "cachedContentTokenCount": 40,
            "candidatesTokenCount": 25,
            "thoughtsTokenCount": 15,
            "totalTokenCount": 140,
        }
    )
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 40


def test_unknown_is_distinct_from_zero():
    assert normalize_usage(None)["prompt_tokens"] is None
    assert normalize_usage({"prompt_tokens": 0, "completion_tokens": 0})["prompt_tokens"] == 0


def test_missing_price_is_unknown():
    from app.domain.models.execution_usage import price_usage

    assert price_usage(100, 50, None, Decimal(2)) is None
    assert price_usage(100, 50, Decimal(1), Decimal(2)) == Decimal("0.0002")


@pytest.mark.parametrize("value", [-1, True, "broken", 1.5])
def test_malformed_usage_retains_unknown(value):
    assert normalize_usage({"prompt_tokens": value})["prompt_tokens"] is None


def test_category_prices_are_fixed_and_missing_cache_is_unknown():
    from app.domain.models.execution_usage import PriceSnapshot

    prices = PriceSnapshot(input_per_million=Decimal(1), output_per_million=Decimal(2))
    usage = normalize_usage(
        {
            "input_tokens": 20,
            "cache_creation_input_tokens": 30,
            "cache_read_input_tokens": 50,
            "output_tokens": 10,
        }
    )
    assert prices.cost(usage) is None
    full = PriceSnapshot(
        input_per_million=Decimal(1),
        output_per_million=Decimal(2),
        cache_read_per_million=Decimal(".1"),
        cache_write_per_million=Decimal("1.25"),
    )
    assert full.cost(usage) == Decimal("0.0000825")
    assert full.revision != prices.revision
    with pytest.raises(ValueError, match="frozen"):
        full.input_per_million = Decimal(7)


def test_snapshot_allowlist_and_actual_prompt_are_immutable():
    from app.application.services.execution_usage_service import configuration_snapshot
    from app.domain.models.inference import (
        InferenceEndpoint,
        InferenceModel,
        ResolvedInferenceModel,
    )

    model = ResolvedInferenceModel(
        model=InferenceModel(
            model_name="alias",
            extra_params={"api_key": "sk-secret-should-not-persist", "custom": "private"},
        ),
        endpoint=InferenceEndpoint(credential="secret-credential"),
    )
    messages = [
        {"role": "system", "content": "resolved skill body"},
        {"role": "user", "content": "hello"},
    ]
    snapshot = configuration_snapshot(
        model, messages=messages, tools=[], policy_revision="fixed", tool_fingerprint="tool-rev"
    )
    encoded = str(snapshot)
    assert "secret-credential" not in encoded
    assert "sk-secret" not in encoded
    assert snapshot["prompt"]["messages"][0]["content"] == "resolved skill body"
    assert snapshot["version_unpinned"] is True
    messages[0]["content"] = "changed"
    assert snapshot["prompt"]["messages"][0]["content"] == "resolved skill body"
    assert snapshot["price"]["input_per_million"] is None  # legacy default zero ambiguous


@pytest.mark.asyncio
async def test_physical_guard_keeps_first_unknown_and_second_actual_revision():
    from app.application.ports.inference_dispatch import dispatch_context
    from app.infrastructure.external.llm.dispatch import physical_send

    class Guard:
        def __init__(self):
            self.sent = []
            self.facts = []

        async def before_send(self, model, payload):
            self.sent.append(payload.copy())
            return str(len(self.sent))

        async def after_send(self, identity, usage, revision):
            self.facts.append((identity, usage, revision))

    guard = Guard()

    async def failed():
        raise TimeoutError("unknown remote outcome")

    async def success():
        return {"usage": {"prompt_tokens": 10, "completion_tokens": 2}, "model": "actual-revision"}

    with dispatch_context(guard, "candidate"):
        with pytest.raises(TimeoutError):
            await physical_send(failed, {"model": "alias"}, provider="openai")
        await physical_send(success, {"model": "alias"}, provider="openai")
    assert len(guard.sent) == 2
    assert guard.facts == [
        ("2", normalize_usage({"prompt_tokens": 10, "completion_tokens": 2}), "actual-revision")
    ]


@pytest.mark.asyncio
async def test_anthropic_internal_cache_fallback_has_two_guarded_sends():
    import httpx

    from app.application.ports.inference_dispatch import dispatch_context
    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM

    adapter = object.__new__(AnthropicLLM)
    adapter._base_url = "https://provider.invalid"
    adapter._credential = "test-only"

    class Transport:
        async def post(self, *args, **kwargs):
            return httpx.Response(
                400 if kwargs["json"].get("system") != "test" else 200,
                json={
                    "usage": {
                        "input_tokens": 20,
                        "cache_read_input_tokens": 50,
                        "cache_creation_input_tokens": 30,
                        "output_tokens": 10,
                    },
                    "model": "revision",
                },
            )

    adapter._client = Transport()

    class Guard:
        def __init__(self):
            self.requests = []
            self.facts = []

        async def before_send(self, model, payload):
            self.requests.append(payload)
            return str(len(self.requests))

        async def after_send(self, identity, usage, revision):
            self.facts.append((identity, usage, revision))

    guard = Guard()
    with dispatch_context(guard, "selected"):
        await adapter._post_messages(
            {"system": [{"type": "text", "text": "test", "cache_control": {"type": "ephemeral"}}]}
        )
    assert len(guard.requests) == 2
    assert guard.facts[-1][1]["prompt_tokens"] == 100
    assert guard.facts[-1][2] == "revision"


def test_missing_cache_category_never_claims_accurate_price():
    from app.domain.models.execution_usage import PriceSnapshot

    price = PriceSnapshot(input_per_million=Decimal(1), output_per_million=Decimal(2))
    assert price.cost(normalize_usage({"prompt_tokens": 100, "completion_tokens": 20})) is None


@pytest.mark.asyncio
async def test_stream_failure_retains_intent_unknown_without_synthesized_zero():
    from app.application.ports.inference_dispatch import dispatch_context
    from app.infrastructure.external.llm.dispatch import physical_send

    class Guard:
        def __init__(self):
            self.count = 0
            self.facts = []

        async def before_send(self, model, payload):
            self.count += 1
            return str(self.count)

        async def after_send(self, *fact):
            self.facts.append(fact)

    async def chunks():
        yield {"model": "revision", "choices": []}
        raise OSError("stream disconnected")

    async def send():
        return chunks()

    guard = Guard()
    with dispatch_context(guard, "model"):
        stream = await physical_send(send, {}, provider="openai")
        with pytest.raises(OSError, match="disconnected"):
            async for _ in stream:
                pass
    assert guard.count == 1
    assert guard.facts == []


@pytest.mark.asyncio
async def test_gemini_adapter_returns_thoughts_and_reported_zero():
    import httpx

    from app.infrastructure.external.llm.gemini_llm import GeminiLLM
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    adapter = GeminiLLM(resolved_chat_model())

    class Transport:
        async def post(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={
                    "modelVersion": "actual",
                    "candidates": [{"content": {"parts": [{"text": "answer"}]}}],
                    "usageMetadata": {
                        "promptTokenCount": 100,
                        "candidatesTokenCount": 25,
                        "thoughtsTokenCount": 15,
                        "cachedContentTokenCount": 40,
                        "totalTokenCount": 140,
                    },
                },
            )

    await adapter._client.aclose()
    adapter._client = Transport()
    result = await adapter.invoke([{"role": "user", "content": "hello"}])
    assert result["_usage"]["completion_tokens"] == 40
    assert result["_model_revision"] == "actual"


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_second", [True, False])
async def test_openai_multimodal_retry_guard_covers_each_transport_and_denial(allow_second):
    from types import SimpleNamespace

    from openai.types.chat import ChatCompletion

    from app.application.ports.inference_dispatch import dispatch_context
    from app.domain.errors import ServerRequestsError
    from app.domain.models.inference import InferenceCapabilities
    from app.infrastructure.external.llm.openai_llm import OpenAILLM
    from tests.app.infrastructure.external.llm.inference_model_factory import resolved_chat_model

    model = resolved_chat_model(capabilities=InferenceCapabilities(vision=True))
    adapter = OpenAILLM(model)
    assert adapter._client.max_retries == 0
    await adapter._client.close()
    sends = []

    async def create(**kwargs):
        sends.append(kwargs)
        if len(sends) == 1:
            raise OSError("connection error")
        return ChatCompletion(
            id="local",
            created=0,
            model="provider-revision",
            object="chat.completion",
            choices=[
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ],
            usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        )

    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    class Guard:
        def __init__(self):
            self.requests = []
            self.facts = []

        async def before_send(self, candidate, payload):
            self.requests.append(payload)
            if len(self.requests) > 1 and not allow_second:
                raise RuntimeError("dispatch denied")
            return str(len(self.requests))

        async def after_send(self, *fact):
            self.facts.append(fact)

    guard = Guard()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "image_url", "image_url": {"url": "https://image.invalid/a.png"}},
            ],
        }
    ]
    with dispatch_context(guard, model):
        if allow_second:
            await adapter.invoke(messages)
        else:
            with pytest.raises(ServerRequestsError, match="dispatch denied"):
                await adapter.invoke(messages)
    assert len(guard.requests) == 2
    assert len(sends) == (2 if allow_second else 1)
    assert len(guard.facts) == (1 if allow_second else 0)


def test_usage_metadata_event_has_frozen_minimal_v1_contract():
    from app.domain.execution.run import ModelUsagePayload, RunAggregate

    registry = RunAggregate()
    assert registry.command_registry.latest_version("RecordModelUsage") == 1
    assert registry.event_registry.latest_version("ModelUsageRecorded") == 1
    assert set(ModelUsagePayload.model_fields) == {"call_identity", "phase"}
    with pytest.raises(ValueError, match="Extra inputs"):
        ModelUsagePayload(
            call_identity="11111111-1111-1111-1111-111111111111", phase="dispatch", cost_usd="0"
        )


def test_transformed_request_snapshot_rejects_unknown_parameter_fields():
    from app.application.services.execution_usage_service import request_snapshot

    snapshot = request_snapshot(
        {
            "model": "alias",
            "temperature": 0.2,
            "max_tokens": 42,
            "custom_vendor_field": "sensitive-unapproved-value",
            "extra_body": {"unknown": "value"},
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    assert "sensitive-unapproved-value" not in str(snapshot)
    assert snapshot["inference"]["max_tokens"] == 42
    assert snapshot["omitted_field_count"] == 2
    assert snapshot["redacted"] is True


@pytest.mark.asyncio
async def test_resource_admission_captures_requester_in_caller_uow_without_chat_resolution():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.application.services.execution_usage_service import ExecutionUsageService
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal

    scope = OwnerScope.personal("original-user")
    auth = AuthorizationContext.for_principal(Principal(user_id="original-user"), scope=scope)
    repository = SimpleNamespace(
        capture_requester=AsyncMock(return_value={"proof": "sealed"}),
        admission_snapshot=AsyncMock(return_value="configuration"),
    )
    work = SimpleNamespace(execution_usage=repository, authorization_context=auth)
    models = SimpleNamespace(resolve_chat=AsyncMock())
    service = ExecutionUsageService(repository_context=None, physical_dispatch=object())
    result = await service.admission_resolver(models)(
        scope,
        "resource-run",
        "policy",
        {"build_id": "build"},
        "production",
        inference_read_context=work,
    )
    assert result == "configuration"
    repository.capture_requester.assert_awaited_once_with(scope, auth, run_id="resource-run")
    models.resolve_chat.assert_not_awaited()
