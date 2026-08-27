"""Provider registry contract tests for the inference control plane."""

import pytest

from app.domain.models.inference import InferenceModelKind, InferenceProvider
from app.infrastructure.external.inference.registry import (
    UnsupportedInferenceCombination,
    provider_spec,
    validate_provider_kind,
)


@pytest.mark.parametrize("provider", list(InferenceProvider))
def test_every_provider_has_exactly_one_registry_entry(provider: InferenceProvider) -> None:
    assert provider_spec(provider).provider is provider


@pytest.mark.parametrize(
    ("provider", "kind"),
    [
        (InferenceProvider.OPENAI, InferenceModelKind.CHAT),
        (InferenceProvider.OPENAI, InferenceModelKind.EMBEDDING),
        (InferenceProvider.AZURE, InferenceModelKind.CHAT),
        (InferenceProvider.AZURE, InferenceModelKind.EMBEDDING),
        (InferenceProvider.OLLAMA, InferenceModelKind.CHAT),
        (InferenceProvider.OLLAMA, InferenceModelKind.EMBEDDING),
        (InferenceProvider.ANTHROPIC, InferenceModelKind.CHAT),
        (InferenceProvider.GEMINI, InferenceModelKind.CHAT),
    ],
)
def test_supported_provider_kind_matrix(
    provider: InferenceProvider,
    kind: InferenceModelKind,
) -> None:
    validate_provider_kind(provider, kind)


@pytest.mark.parametrize("provider", [InferenceProvider.ANTHROPIC, InferenceProvider.GEMINI])
def test_unsupported_embedding_provider_is_rejected_at_mutation_time(
    provider: InferenceProvider,
) -> None:
    with pytest.raises(UnsupportedInferenceCombination) as exc_info:
        validate_provider_kind(provider, InferenceModelKind.EMBEDDING)
    assert exc_info.value.provider is provider
    assert exc_info.value.kind is InferenceModelKind.EMBEDDING


def test_ollama_is_the_only_provider_without_required_credentials() -> None:
    optional = {
        provider
        for provider in InferenceProvider
        if not provider_spec(provider).credential_required
    }
    assert optional == {InferenceProvider.OLLAMA}
