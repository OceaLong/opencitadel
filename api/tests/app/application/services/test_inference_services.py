from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.ports.crypto import OutboundNetworkPolicy
from app.application.ports.inference import UnsupportedInferenceCombination
from app.application.services.inference_binding_service import InferenceBindingService
from app.application.services.inference_endpoint_service import InferenceEndpointService
from app.application.services.inference_model_service import InferenceModelService
from app.domain.errors import BadRequestError, ConflictError
from app.domain.models.inference import (
    ChatModelSettings,
    EmbeddingModelSettings,
    InferenceBinding,
    InferenceEndpoint,
    InferenceModel,
    InferenceModelKind,
    InferenceProbeStatus,
    InferenceProvider,
    InferencePurpose,
)
from app.domain.models.scope import OwnerScope
from app.infrastructure.security.api_key_cipher import ApiKeyCipher


class _EndpointRepo:
    def __init__(self, endpoints: list[InferenceEndpoint] = ()) -> None:
        self.items = {item.id: item for item in endpoints}

    async def get_by_id(self, endpoint_id: str, scope=None):
        return self.items.get(endpoint_id)


class _ModelRepo:
    def __init__(self, models: list[InferenceModel] = ()) -> None:
        self.items = {item.id: item for item in models}

    async def get_by_id(self, model_id: str, scope=None):
        return self.items.get(model_id)

    async def get_all(self, scope=None):
        return list(self.items.values())


class _BindingRepo:
    def __init__(self, bindings: list[InferenceBinding] = ()) -> None:
        self.items = {item.purpose: item for item in bindings}
        self.saved: list[InferenceBinding] = []

    async def get_effective_binding(self, purpose, scope):
        return self.items.get(purpose)

    async def get_exact(self, purpose, scope):
        return self.items.get(purpose)

    async def get_all_effective(self, scope):
        return list(self.items.values())

    async def save(self, binding, scope):
        self.items[binding.purpose] = binding
        self.saved.append(binding)

    async def delete_scoped_binding(self, purpose, scope):
        self.items.pop(purpose, None)


@dataclass
class _UoW:
    inference_endpoint: _EndpointRepo
    inference_model: _ModelRepo
    inference_binding: _BindingRepo
    committed: bool = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def commit(self) -> None:
        self.committed = True


@dataclass
class _UoWFactory:
    endpoints: list[InferenceEndpoint] = field(default_factory=list)
    models: list[InferenceModel] = field(default_factory=list)
    bindings: list[InferenceBinding] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.uow = _UoW(
            _EndpointRepo(self.endpoints),
            _ModelRepo(self.models),
            _BindingRepo(self.bindings),
        )

    def __call__(self):
        return self.uow


def _endpoint(
    *,
    provider: InferenceProvider = InferenceProvider.OPENAI,
    credential: str = "secret",
) -> InferenceEndpoint:
    return InferenceEndpoint(
        id="endpoint-1",
        display_name="endpoint",
        provider=provider,
        base_url="https://api.example.com/v1",
        credential=credential,
    )


def _chat_model() -> InferenceModel:
    return InferenceModel(
        id="chat-1",
        endpoint_id="endpoint-1",
        display_name="chat",
        model_name="chat-model",
        kind=InferenceModelKind.CHAT,
        settings=ChatModelSettings(),
    )


def _embedding_model() -> InferenceModel:
    return InferenceModel(
        id="embedding-1",
        endpoint_id="endpoint-1",
        display_name="embedding",
        model_name="embedding-model",
        kind=InferenceModelKind.EMBEDDING,
        settings=EmbeddingModelSettings(),
    )


class _ProviderPorts:
    def __init__(self, *, chat_adapter=None, embedding_adapter=None) -> None:
        self.chat_adapter = chat_adapter
        self.embedding_adapter = embedding_adapter

    @staticmethod
    def credential_required(provider: InferenceProvider) -> bool:
        return provider is not InferenceProvider.OLLAMA

    @staticmethod
    def ensure_kind_supported(
        provider: InferenceProvider,
        kind: InferenceModelKind,
    ) -> None:
        if provider is InferenceProvider.ANTHROPIC and kind is InferenceModelKind.EMBEDDING:
            raise UnsupportedInferenceCombination(provider, kind)

    def create_model_client(self, _resolved, *, thinking_enabled: bool = False):
        del thinking_enabled
        if self.chat_adapter is None:
            raise ValueError("chat adapter unavailable")
        return self.chat_adapter

    def create_embedding(self, _resolved):
        if self.embedding_adapter is None:
            raise ValueError("embedding adapter unavailable")
        return self.embedding_adapter


def _model_service(factory: _UoWFactory, providers: _ProviderPorts | None = None):
    ports = providers or _ProviderPorts()
    return InferenceModelService(factory, ports, ports, ports)


def test_endpoint_validation_requires_credentials_before_persistence() -> None:
    service = InferenceEndpointService(
        _UoWFactory(),
        ApiKeyCipher("e" * 32),
        OutboundNetworkPolicy(allowed_ports=frozenset({443})),
        _ProviderPorts(),
    )

    with pytest.raises(BadRequestError) as exc_info:
        service.validate(_endpoint(credential=""))

    assert exc_info.value.error_key == "inference.errors.credentialRequired"


def test_ollama_endpoint_allows_an_empty_credential() -> None:
    service = InferenceEndpointService(
        _UoWFactory(),
        ApiKeyCipher("e" * 32),
        OutboundNetworkPolicy(allowed_ports=frozenset({443})),
        _ProviderPorts(),
    )

    service.validate(_endpoint(provider=InferenceProvider.OLLAMA, credential=""))


def test_model_validation_rejects_unsupported_provider_kind() -> None:
    service = _model_service(_UoWFactory())

    with pytest.raises(BadRequestError) as exc_info:
        service.validate(_embedding_model(), _endpoint(provider=InferenceProvider.ANTHROPIC))

    assert exc_info.value.error_key == "inference.errors.unsupportedProviderKind"


@pytest.mark.asyncio
async def test_binding_rejects_model_kind_mismatch() -> None:
    factory = _UoWFactory(endpoints=[_endpoint()], models=[_embedding_model()])
    service = InferenceBindingService(factory, _ProviderPorts())

    with pytest.raises(BadRequestError) as exc_info:
        await service.set_binding(
            InferencePurpose.CHAT,
            "embedding-1",
            scope=OwnerScope.personal("user-1"),
        )

    assert exc_info.value.error_key == "inference.errors.bindingKindMismatch"
    assert factory.uow.inference_binding.saved == []


@pytest.mark.asyncio
async def test_missing_binding_is_explicit_not_configured_error() -> None:
    service = InferenceBindingService(_UoWFactory(), _ProviderPorts())

    with pytest.raises(ConflictError) as exc_info:
        await service.resolve(
            InferencePurpose.EMBEDDING,
            scope=OwnerScope.personal("user-1"),
        )

    assert exc_info.value.error_key == "inference.errors.bindingNotConfigured"


@pytest.mark.asyncio
async def test_resolve_hydrates_endpoint_credential_only_for_invocation() -> None:
    binding = InferenceBinding(purpose=InferencePurpose.CHAT, model_id="chat-1")
    service = InferenceBindingService(
        _UoWFactory(
            endpoints=[_endpoint(credential="resolved-secret")],
            models=[_chat_model()],
            bindings=[binding],
        ),
        _ProviderPorts(),
    )

    resolved = await service.resolve(InferencePurpose.CHAT, scope=None)

    assert resolved.model.id == "chat-1"
    assert resolved.endpoint.credential == "resolved-secret"
    assert resolved.binding == binding


@pytest.mark.asyncio
async def test_rerank_explicitly_falls_back_to_chat_binding() -> None:
    chat_binding = InferenceBinding(purpose=InferencePurpose.CHAT, model_id="chat-1")
    service = InferenceBindingService(
        _UoWFactory(
            endpoints=[_endpoint()],
            models=[_chat_model()],
            bindings=[chat_binding],
        ),
        _ProviderPorts(),
    )

    resolved = await service.resolve(InferencePurpose.RERANK, scope=None)

    assert resolved.model.id == "chat-1"
    assert resolved.binding == chat_binding


@pytest.mark.asyncio
async def test_model_service_resolves_default_chat_through_binding() -> None:
    chat_binding = InferenceBinding(purpose=InferencePurpose.CHAT, model_id="chat-1")
    service = _model_service(
        _UoWFactory(
            endpoints=[_endpoint()],
            models=[_chat_model(), _embedding_model()],
            bindings=[chat_binding],
        )
    )

    resolved = await service.resolve_chat(scope=OwnerScope.personal("user-1"))
    candidates = await service.list_resolved_chat_models(scope=OwnerScope.personal("user-1"))

    assert resolved.model.id == "chat-1"
    assert [candidate.model.id for candidate in candidates] == ["chat-1"]


@pytest.mark.asyncio
async def test_chat_model_probe_uses_injected_adapter() -> None:
    adapter = SimpleNamespace(invoke=AsyncMock(return_value={"content": "ok"}))
    service = _model_service(
        _UoWFactory(endpoints=[_endpoint()], models=[_chat_model()]),
        _ProviderPorts(chat_adapter=adapter),
    )

    result = await service.probe_model(
        "chat-1",
        scope=OwnerScope.personal("user-1"),
    )

    assert result.status is InferenceProbeStatus.OK
    adapter.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_embedding_probe_rejects_wrong_dimensions() -> None:
    adapter = SimpleNamespace(
        embed_batch=AsyncMock(return_value=[[0.0] * 8]),
    )
    service = _model_service(
        _UoWFactory(endpoints=[_endpoint()], models=[_embedding_model()]),
        _ProviderPorts(embedding_adapter=adapter),
    )

    result = await service.probe_model(
        "embedding-1",
        scope=OwnerScope.personal("user-1"),
    )

    assert result.status is InferenceProbeStatus.ERROR
    assert result.error_key == "inference.errors.embeddingDimensionMismatch"
