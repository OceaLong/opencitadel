from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.application.ports.crypto import OutboundNetworkPolicy
from app.domain.models.inference import (
    InferenceModelKind,
    InferenceProvider,
    ResolvedInferenceModel,
)
from app.infrastructure.security.outbound_http import DEFAULT_OUTBOUND_NETWORK_POLICY

InferenceFactory = Callable[..., Any]


class UnsupportedInferenceCombination(ValueError):
    def __init__(self, provider: InferenceProvider, kind: InferenceModelKind) -> None:
        self.provider = provider
        self.kind = kind
        super().__init__(f"{provider.value} does not support {kind.value} inference models")


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    provider: InferenceProvider
    supported_kinds: frozenset[InferenceModelKind]
    credential_required: bool
    default_base_url: str
    chat_factory: InferenceFactory | None = None
    embedding_factory: InferenceFactory | None = None


def _openai_chat(
    model: ResolvedInferenceModel,
    *,
    thinking_enabled: bool = False,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
):
    from app.infrastructure.external.llm.openai_llm import OpenAILLM

    return OpenAILLM(
        model,
        thinking_enabled=thinking_enabled,
        outbound_policy=outbound_policy,
    )


def _anthropic_chat(
    model: ResolvedInferenceModel,
    *,
    thinking_enabled: bool = False,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
):
    from app.infrastructure.external.llm.anthropic_llm import AnthropicLLM

    return AnthropicLLM(
        model,
        thinking_enabled=thinking_enabled,
        outbound_policy=outbound_policy,
    )


def _gemini_chat(
    model: ResolvedInferenceModel,
    *,
    thinking_enabled: bool = False,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
):
    from app.infrastructure.external.llm.gemini_llm import GeminiLLM

    return GeminiLLM(
        model,
        thinking_enabled=thinking_enabled,
        outbound_policy=outbound_policy,
    )


def _openai_embedding(
    model: ResolvedInferenceModel,
    *,
    outbound_policy: OutboundNetworkPolicy = DEFAULT_OUTBOUND_NETWORK_POLICY,
):
    from app.infrastructure.external.inference.embedding import OpenAICompatibleEmbedding

    return OpenAICompatibleEmbedding(model, outbound_policy=outbound_policy)


_CHAT = frozenset({InferenceModelKind.CHAT})
_CHAT_AND_EMBEDDING = frozenset({InferenceModelKind.CHAT, InferenceModelKind.EMBEDDING})

_PROVIDERS: dict[InferenceProvider, ProviderSpec] = {
    InferenceProvider.OPENAI: ProviderSpec(
        provider=InferenceProvider.OPENAI,
        supported_kinds=_CHAT_AND_EMBEDDING,
        credential_required=True,
        default_base_url="https://api.openai.com/v1",
        chat_factory=_openai_chat,
        embedding_factory=_openai_embedding,
    ),
    InferenceProvider.AZURE: ProviderSpec(
        provider=InferenceProvider.AZURE,
        supported_kinds=_CHAT_AND_EMBEDDING,
        credential_required=True,
        default_base_url="",
        chat_factory=_openai_chat,
        embedding_factory=_openai_embedding,
    ),
    InferenceProvider.OLLAMA: ProviderSpec(
        provider=InferenceProvider.OLLAMA,
        supported_kinds=_CHAT_AND_EMBEDDING,
        credential_required=False,
        default_base_url="http://localhost:11434/v1",
        chat_factory=_openai_chat,
        embedding_factory=_openai_embedding,
    ),
    InferenceProvider.ANTHROPIC: ProviderSpec(
        provider=InferenceProvider.ANTHROPIC,
        supported_kinds=_CHAT,
        credential_required=True,
        default_base_url="https://api.anthropic.com",
        chat_factory=_anthropic_chat,
    ),
    InferenceProvider.GEMINI: ProviderSpec(
        provider=InferenceProvider.GEMINI,
        supported_kinds=_CHAT,
        credential_required=True,
        default_base_url="https://generativelanguage.googleapis.com",
        chat_factory=_gemini_chat,
    ),
}


def provider_spec(provider: InferenceProvider) -> ProviderSpec:
    return _PROVIDERS[provider]


def validate_provider_kind(
    provider: InferenceProvider,
    kind: InferenceModelKind,
) -> None:
    if kind not in provider_spec(provider).supported_kinds:
        raise UnsupportedInferenceCombination(provider, kind)
