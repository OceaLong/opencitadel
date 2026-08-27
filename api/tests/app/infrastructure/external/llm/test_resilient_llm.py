import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.errors import ServerRequestsError
from app.domain.models.error_codes import MODEL_QUOTA_EXCEEDED
from app.domain.models.inference import (
    ChatModelSettings,
    InferenceCapabilities,
    InferenceEndpoint,
    InferenceModel,
    InferenceProvider,
    ResolvedInferenceModel,
)
from app.domain.runtime_policy import ModelResiliencePolicy
from app.infrastructure.external.llm.resilient_llm import (
    ModelUnavailableError,
)
from app.infrastructure.external.llm.resilient_llm import (
    ResilientLLMClient as _ProductionResilientLLMClient,
)
from tests.app.application_test_support import FakeModelMetrics


class _FakeLLM:
    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.response = response or {"content": "ok"}
        self.error = error
        self.invoke_count = 0

    model_name = "fake"
    temperature = 0.7
    max_tokens = 1024
    supports_multimodal = False

    @property
    def capabilities(self):
        return InferenceCapabilities()

    async def invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
        response_schema=None,
    ):
        self.invoke_count += 1
        if self.error is not None:
            raise self.error
        return self.response

    async def stream_invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
        response_schema=None,
    ):
        yield {"content": "hello"}
        raise RuntimeError("503 service unavailable")


def _model(
    model_id: str,
    *,
    provider: InferenceProvider = InferenceProvider.OPENAI,
    credential: str = "sk-test",
    endpoint_id: str | None = None,
    extra_params: dict | None = None,
) -> ResolvedInferenceModel:
    resolved_endpoint_id = endpoint_id or f"endpoint-{model_id}"
    return ResolvedInferenceModel(
        model=InferenceModel(
            id=model_id,
            endpoint_id=resolved_endpoint_id,
            display_name=model_id,
            model_name=f"gpt-{model_id}",
            settings=ChatModelSettings(),
            extra_params=extra_params or {},
        ),
        endpoint=InferenceEndpoint(
            id=resolved_endpoint_id,
            display_name=resolved_endpoint_id,
            provider=provider,
            base_url="http://localhost",
            credential=credential,
        ),
    )


def _policy(
    *,
    fallback_enabled: bool = True,
    fallback_on_quota_exceeded: bool = True,
    allow_cross_provider_fallback: bool = False,
    allow_cross_provider_fallback_on_quota: bool = True,
    max_attempts_per_call: int = 1,
):
    return ModelResiliencePolicy(
        enabled=True,
        fallback_enabled=fallback_enabled,
        fallback_on_quota_exceeded=fallback_on_quota_exceeded,
        allow_cross_provider_fallback=allow_cross_provider_fallback,
        allow_cross_provider_fallback_on_quota=allow_cross_provider_fallback_on_quota,
        max_attempts_per_call=max_attempts_per_call,
        max_call_budget_seconds=120.0,
        fast_fail_on_open_circuit=True,
    )


class ResilientLLMClient(_ProductionResilientLLMClient):
    """Test constructor that makes each test's immutable policy explicit."""

    def __init__(self, inner, model, *, policy=None, **kwargs):
        breaker = MagicMock()
        breaker.allow_request = AsyncMock(return_value="allow")
        breaker.record_success = AsyncMock()
        breaker.record_failure = AsyncMock()
        provider_catalog = MagicMock()
        provider_catalog.credential_required.side_effect = lambda provider: (
            provider is not InferenceProvider.OLLAMA
        )
        model_client_factory = MagicMock()
        super().__init__(
            inner,
            model,
            policy=policy or _policy(),
            breaker=kwargs.pop("breaker", breaker),
            provider_catalog=kwargs.pop("provider_catalog", provider_catalog),
            model_client_factory=kwargs.pop(
                "model_client_factory",
                model_client_factory,
            ),
            metrics=kwargs.pop("metrics", FakeModelMetrics()),
            **kwargs,
        )


QUOTA_ERROR = RuntimeError(
    "Error code: 403 - {'error': {'code': 'insufficient_quota', "
    "'message': 'The free quota has been exhausted.'}}"
)


async def _consume_stream(client, messages, chunks):
    async for chunk in client.stream_invoke(messages):
        chunks.extend((chunk,))


async def _test_stream_invoke_no_midstream_fallback_after_delta():
    model = _model("m1")
    client = ResilientLLMClient(_FakeLLM(), model)
    chunks = []
    with pytest.raises(ModelUnavailableError, match="503 service unavailable"):
        await _consume_stream(client, [{"role": "user", "content": "hi"}], chunks)
    assert chunks == [{"content": "hello"}]
    assert client.streaming_started is True


def test_stream_invoke_no_midstream_fallback_after_delta():
    asyncio.run(_test_stream_invoke_no_midstream_fallback_after_delta())


async def _test_open_primary_falls_back_to_allowed_candidate():
    primary = _model("m1")
    fallback = _model("m2")
    fallback_llm = _FakeLLM(response={"content": "fallback"})
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(side_effect=["deny", "allow"])
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        _FakeLLM(response={"content": "primary"}),
        primary,
        inference_model_service=inference_model_service,
    )
    client._breaker = breaker

    with (
        patch.object(client, "_policy", _policy()),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "fallback"}
    assert fallback_llm.invoke_count == 1
    breaker.record_success.assert_awaited_once_with("m2", client._policy)


def test_open_primary_falls_back_to_allowed_candidate():
    asyncio.run(_test_open_primary_falls_back_to_allowed_candidate())


async def _test_open_primary_without_fallback_fast_fails():
    primary = _model("m1")
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="deny")

    client = ResilientLLMClient(_FakeLLM(), primary)
    client._breaker = breaker

    with (
        patch.object(client, "_policy", _policy(fallback_enabled=False)),
        pytest.raises(ModelUnavailableError),
    ):
        await client.invoke([{"role": "user", "content": "hi"}])


def test_open_primary_without_fallback_fast_fails():
    asyncio.run(_test_open_primary_without_fallback_fast_fails())


async def _test_non_retriable_request_error_does_not_fallback():
    primary = _model("m1")
    fallback = _model("m2")
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        _FakeLLM(error=RuntimeError("400 bad request invalid model")),
        primary,
        inference_model_service=inference_model_service,
    )
    client._breaker = breaker

    with (
        patch.object(client, "_policy", _policy()),
        patch.object(
            client._model_client_factory,
            "create_model_client",
        ) as create,
        pytest.raises(ModelUnavailableError),
    ):
        await client.invoke([{"role": "user", "content": "hi"}])

    create.assert_not_called()


def test_non_retriable_request_error_does_not_fallback():
    asyncio.run(_test_non_retriable_request_error_does_not_fallback())


async def _test_candidate_chain_is_cached_per_vision_requirement():
    primary = _model("m1")
    fallback = _model("m2")
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    client = ResilientLLMClient(
        _FakeLLM(), primary, inference_model_service=inference_model_service
    )

    with patch.object(client, "_policy", _policy()):
        first = await client._build_candidate_chain(require_vision=False)
        second = await client._build_candidate_chain(require_vision=False)

    assert [m.id for m in first] == ["m1", "m2"]
    assert [m.id for m in second] == ["m1", "m2"]
    inference_model_service.list_resolved_chat_models.assert_awaited_once_with(scope=None)


def test_candidate_chain_is_cached_per_vision_requirement():
    asyncio.run(_test_candidate_chain_is_cached_per_vision_requirement())


async def _test_streaming_started_resets_between_calls():
    model = _model("m1")
    client = ResilientLLMClient(_FakeLLM(), model)
    with pytest.raises(ModelUnavailableError, match="503 service unavailable"):
        await _consume_stream(client, [{"role": "user", "content": "hi"}], [])
    assert client.streaming_started is True

    chunks = []
    with pytest.raises(ModelUnavailableError, match="503 service unavailable"):
        await _consume_stream(client, [{"role": "user", "content": "again"}], chunks)
    assert chunks == [{"content": "hello"}]
    assert client.streaming_started is True


def test_streaming_started_resets_between_calls():
    asyncio.run(_test_streaming_started_resets_between_calls())


async def _test_response_schema_is_forwarded():
    primary = _model("m1")
    llm = _FakeLLM(response={"content": "{}"})
    client = ResilientLLMClient(llm, primary)
    with patch.object(client, "_policy", _policy(fallback_enabled=False)):
        result = await client.invoke(
            [{"role": "user", "content": "hi"}],
            response_schema={"name": "Result", "schema": {}},
        )

    assert result == {"content": "{}"}


def test_response_schema_is_forwarded():
    asyncio.run(_test_response_schema_is_forwarded())


class _RetryThenSucceedLLM:
    def __init__(self, *, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.invoke_count = 0

    model_name = "fake"
    temperature = 0.7
    max_tokens = 1024
    supports_multimodal = False

    @property
    def capabilities(self):
        return InferenceCapabilities()

    async def invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
        response_schema=None,
    ):
        self.invoke_count += 1
        if self.invoke_count <= self.fail_times:
            raise RuntimeError("503 service unavailable")
        return {"content": "ok"}


class _SuccessStreamLLM:
    model_name = "fake"
    temperature = 0.7
    max_tokens = 1024
    supports_multimodal = False

    @property
    def capabilities(self):
        return InferenceCapabilities()

    async def stream_invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
        response_schema=None,
    ):
        yield {"content": "hello"}


async def _test_retriable_invoke_failure_uses_configured_attempts():
    primary = _model("m1")
    llm = _RetryThenSucceedLLM(fail_times=1)
    client = ResilientLLMClient(llm, primary)
    with patch.object(
        client,
        "_policy",
        _policy(fallback_enabled=False, max_attempts_per_call=3),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "ok"}
    assert llm.invoke_count == 2


def test_retriable_invoke_failure_uses_configured_attempts():
    asyncio.run(_test_retriable_invoke_failure_uses_configured_attempts())


async def _test_successful_stream_calls_are_independent():
    primary = _model("m1")
    client = ResilientLLMClient(_SuccessStreamLLM(), primary)
    with patch.object(client, "_policy", _policy(fallback_enabled=False)):
        for _ in range(15):
            chunks = [
                chunk
                async for chunk in client.stream_invoke(
                    [{"role": "user", "content": "hi"}],
                )
            ]
            assert chunks == [{"content": "hello"}]


def test_successful_stream_calls_are_independent():
    asyncio.run(_test_successful_stream_calls_are_independent())


async def _test_invoke_fallback_follows_configured_attempts():
    primary = _model("m1")
    fallback = _model("m2")
    primary_llm = _RetryThenSucceedLLM(fail_times=99)
    fallback_llm = _FakeLLM(response={"content": "fallback"})
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker
    with (
        patch.object(client, "_policy", _policy(max_attempts_per_call=2)),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "fallback"}
    assert primary_llm.invoke_count == 2


def test_invoke_fallback_follows_configured_attempts():
    asyncio.run(_test_invoke_fallback_follows_configured_attempts())


async def _test_quota_exhausted_falls_back_without_general_fallback():
    primary = _model("m1")
    fallback = _model("m2")
    primary_llm = _FakeLLM(error=QUOTA_ERROR)
    fallback_llm = _FakeLLM(response={"content": "fallback"})
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "fallback"}
    assert client.active_model.id == "m2"
    assert fallback_llm.invoke_count == 1


def test_quota_exhausted_falls_back_without_general_fallback():
    asyncio.run(_test_quota_exhausted_falls_back_without_general_fallback())


async def _test_quota_exhausted_falls_back_through_server_requests_error():
    primary = _model("m1")
    fallback = _model("m2")
    quota_wrapped = ServerRequestsError("调用LLM失败: 模型 gpt-m1 API 配额已耗尽")
    primary_llm = _FakeLLM(error=quota_wrapped)
    fallback_llm = _FakeLLM(response={"content": "fallback"})
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "fallback"}
    assert client.active_model.id == "m2"
    assert fallback_llm.invoke_count == 1


def test_quota_exhausted_falls_back_through_server_requests_error():
    asyncio.run(_test_quota_exhausted_falls_back_through_server_requests_error())


async def _test_stream_quota_exhausted_falls_back_through_server_requests_error():
    primary = _model("m1")
    fallback = _model("m2")
    quota_wrapped = ServerRequestsError("调用LLM失败: 模型 gpt-m1 API 配额已耗尽")

    class _QuotaStreamLLM:
        model_name = "fake"
        temperature = 0.7
        max_tokens = 1024
        supports_multimodal = False

        @property
        def capabilities(self):
            return InferenceCapabilities()

        async def stream_invoke(self, *args, **kwargs):
            raise quota_wrapped
            yield  # pragma: no cover

        async def invoke(self, *args, **kwargs):
            raise quota_wrapped

    primary_llm = _QuotaStreamLLM()
    fallback_llm = _SuccessStreamLLM()
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    chunks = []
    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        chunks.extend(
            [chunk async for chunk in client.stream_invoke([{"role": "user", "content": "hi"}])]
        )

    assert chunks == [{"content": "hello"}]
    assert client.active_model.id == "m2"


def test_stream_quota_exhausted_falls_back_through_server_requests_error():
    asyncio.run(_test_stream_quota_exhausted_falls_back_through_server_requests_error())


async def _test_quota_exhausted_without_candidates_raises_quota_code():
    primary = _model("m1")
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        _FakeLLM(error=QUOTA_ERROR), primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        pytest.raises(ModelUnavailableError) as exc_info,
    ):
        await client.invoke([{"role": "user", "content": "hi"}])

    assert exc_info.value.error_code == MODEL_QUOTA_EXCEEDED


def test_quota_exhausted_without_candidates_raises_quota_code():
    asyncio.run(_test_quota_exhausted_without_candidates_raises_quota_code())


async def _test_quota_exhausted_cross_provider_fallback():
    primary = _model("m1")
    fallback = _model(
        "m2",
        provider=InferenceProvider.OLLAMA,
        credential="",
    )
    primary_llm = _FakeLLM(error=QUOTA_ERROR)
    fallback_llm = _FakeLLM(response={"content": "ollama"})
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(
                fallback_enabled=False,
                fallback_on_quota_exceeded=True,
                allow_cross_provider_fallback_on_quota=True,
            ),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "ollama"}
    assert client.active_model.provider == InferenceProvider.OLLAMA


def test_quota_exhausted_cross_provider_fallback():
    asyncio.run(_test_quota_exhausted_cross_provider_fallback())


class _QuotaThenSuccessStreamLLM:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    model_name = "fake"
    temperature = 0.7
    max_tokens = 1024
    supports_multimodal = False

    @property
    def capabilities(self):
        return InferenceCapabilities()

    async def stream_invoke(
        self,
        messages,
        tools=None,
        response_format=None,
        tool_choice=None,
        response_schema=None,
    ):
        if self.error is not None:
            raise self.error
        yield {"content": "hello"}


async def _test_stream_quota_fallback_before_first_token():
    primary = _model("m1")
    fallback = _model("m2")
    primary_llm = _QuotaThenSuccessStreamLLM(error=QUOTA_ERROR)
    fallback_llm = _QuotaThenSuccessStreamLLM()
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        chunks = [
            chunk async for chunk in client.stream_invoke([{"role": "user", "content": "hi"}])
        ]

    assert chunks == [{"content": "hello"}]
    assert client.active_model.id == "m2"


def test_stream_quota_fallback_before_first_token():
    asyncio.run(_test_stream_quota_fallback_before_first_token())


async def _test_all_same_endpoint_models_quota_exhausted_raises_quota_code():
    endpoint_id = "shared-endpoint"
    primary = _model("m1", endpoint_id=endpoint_id)
    second = _model("m2", endpoint_id=endpoint_id)
    third = _model("m3", endpoint_id=endpoint_id)
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(
        return_value=[primary, second, third]
    )
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    def _create(model, **kwargs):
        return _FakeLLM(error=QUOTA_ERROR)

    client = ResilientLLMClient(
        _FakeLLM(error=QUOTA_ERROR), primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            side_effect=_create,
        ),
        pytest.raises(ModelUnavailableError) as exc_info,
    ):
        await client.invoke([{"role": "user", "content": "hi"}])

    assert exc_info.value.error_code == MODEL_QUOTA_EXCEEDED
    assert client._quota_exhausted_model_ids == {"m1", "m2", "m3"}


def test_all_same_endpoint_models_quota_exhausted_raises_quota_code():
    asyncio.run(_test_all_same_endpoint_models_quota_exhausted_raises_quota_code())


async def _test_same_endpoint_quota_fallback_to_available_model():
    endpoint_id = "shared-endpoint"
    primary = _model("m1", endpoint_id=endpoint_id)
    second = _model("m2", endpoint_id=endpoint_id)
    third = _model("m3", endpoint_id=endpoint_id)
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(
        return_value=[primary, second, third]
    )
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()
    third_llm = _FakeLLM(response={"content": "from-m3"})

    def _create(model, **kwargs):
        if model.id == "m3":
            return third_llm
        return _FakeLLM(error=QUOTA_ERROR)

    client = ResilientLLMClient(
        _FakeLLM(error=QUOTA_ERROR), primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            side_effect=_create,
        ),
    ):
        result = await client.invoke([{"role": "user", "content": "hi"}])

    assert result == {"content": "from-m3"}
    assert client.active_model.id == "m3"
    assert third_llm.invoke_count == 1


def test_same_endpoint_quota_fallback_to_available_model():
    asyncio.run(_test_same_endpoint_quota_fallback_to_available_model())


async def _test_quota_exhausted_model_skipped_on_subsequent_invoke():
    primary = _model("m1")
    fallback = _model("m2")
    primary_llm = _FakeLLM(error=QUOTA_ERROR)
    fallback_llm = _FakeLLM(response={"content": "fallback"})
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(return_value=[primary, fallback])
    breaker = MagicMock()
    breaker.allow_request = AsyncMock(return_value="allow")
    breaker.record_success = AsyncMock()
    breaker.record_failure = AsyncMock()

    client = ResilientLLMClient(
        primary_llm, primary, inference_model_service=inference_model_service
    )
    client._breaker = breaker

    with (
        patch.object(
            client,
            "_policy",
            _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
        ),
        patch.object(
            client._model_client_factory,
            "create_model_client",
            return_value=fallback_llm,
        ),
    ):
        first = await client.invoke([{"role": "user", "content": "hi"}])
        assert first == {"content": "fallback"}
        assert primary_llm.invoke_count == 1

        second = await client.invoke([{"role": "user", "content": "again"}])
        assert second == {"content": "fallback"}
        assert primary_llm.invoke_count == 1
        assert fallback_llm.invoke_count == 2


def test_quota_exhausted_model_skipped_on_subsequent_invoke():
    asyncio.run(_test_quota_exhausted_model_skipped_on_subsequent_invoke())


def test_thinking_enabled_for_requires_extra_params():
    model = _model("m1")
    assert ResilientLLMClient._thinking_enabled_for(model, session_thinking=False) is False
    assert ResilientLLMClient._thinking_enabled_for(model, session_thinking=True) is False

    model = _model(
        "m1",
        extra_params={"thinking_request_params": {"enable_thinking": True}},
    )
    assert ResilientLLMClient._thinking_enabled_for(model, session_thinking=True) is True


async def _test_build_candidate_chain_prefers_thinking_configured_models():
    primary = _model("m1")
    plain = _model("m-plain")
    thinking = _model(
        "m-think",
        extra_params={"thinking_request_params": {"enable_thinking": True}},
    )
    inference_model_service = MagicMock()
    inference_model_service.list_resolved_chat_models = AsyncMock(
        return_value=[primary, plain, thinking]
    )

    client = ResilientLLMClient(
        _FakeLLM(),
        primary,
        inference_model_service=inference_model_service,
        thinking_enabled=True,
    )

    with patch.object(
        client,
        "_policy",
        _policy(fallback_enabled=False, fallback_on_quota_exceeded=True),
    ):
        chain = await client._build_candidate_chain(require_vision=False)

    assert [model.id for model in chain] == ["m1", "m-think", "m-plain"]


def test_build_candidate_chain_prefers_thinking_configured_models():
    asyncio.run(_test_build_candidate_chain_prefers_thinking_configured_models())


async def _test_client_for_caches_fallback_clients():
    primary = _model("m1")
    fallback = _model("m2")
    client = ResilientLLMClient(_FakeLLM(), primary, thinking_enabled=True)
    created = _FakeLLM()

    with patch.object(
        client._model_client_factory,
        "create_model_client",
        return_value=created,
    ) as create_mock:
        first = client._client_for(fallback)
        second = client._client_for(fallback)

    assert first is second
    assert create_mock.call_count == 1
    create_mock.assert_called_once_with(fallback, thinking_enabled=False)


def test_client_for_caches_fallback_clients():
    asyncio.run(_test_client_for_caches_fallback_clients())
