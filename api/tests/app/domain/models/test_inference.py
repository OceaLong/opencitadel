from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    ChatModelSettings,
    EmbeddingModelSettings,
    InferenceBinding,
    InferenceEndpoint,
    InferenceModel,
    InferenceModelKind,
    InferenceProvider,
    InferencePurpose,
    ResolvedInferenceModel,
    ResourceVisibility,
    purpose_accepts_kind,
)


def test_embedding_settings_require_platform_dimensions() -> None:
    assert EmbeddingModelSettings().dimensions == PLATFORM_EMBEDDING_DIMENSIONS == 1536
    with pytest.raises(ValidationError):
        EmbeddingModelSettings(dimensions=768)


def test_binding_kind_rules_are_closed() -> None:
    assert purpose_accepts_kind(InferencePurpose.CHAT, InferenceModelKind.CHAT)
    assert purpose_accepts_kind(InferencePurpose.EMBEDDING, InferenceModelKind.EMBEDDING)
    assert purpose_accepts_kind(InferencePurpose.RERANK, InferenceModelKind.CHAT)
    assert not purpose_accepts_kind(InferencePurpose.CHAT, InferenceModelKind.EMBEDDING)
    assert not purpose_accepts_kind(InferencePurpose.EMBEDDING, InferenceModelKind.CHAT)
    assert not purpose_accepts_kind(InferencePurpose.RERANK, InferenceModelKind.EMBEDDING)


def test_model_contract_does_not_duplicate_endpoint_connection_or_secret() -> None:
    assert "credential" not in InferenceModel.model_fields
    assert "api_key" not in InferenceModel.model_fields
    assert "base_url" not in InferenceModel.model_fields
    assert "provider" not in InferenceModel.model_fields


def test_model_settings_are_discriminated_by_kind() -> None:
    chat = InferenceModel(
        endpoint_id="endpoint-1",
        display_name="chat",
        model_name="gpt-4o",
        kind=InferenceModelKind.CHAT,
        settings=ChatModelSettings(temperature=0.2),
    )
    embedding = InferenceModel(
        endpoint_id="endpoint-1",
        display_name="embedding",
        model_name="text-embedding-3-small",
        kind=InferenceModelKind.EMBEDDING,
        settings=EmbeddingModelSettings(),
    )

    assert isinstance(chat.settings, ChatModelSettings)
    assert isinstance(embedding.settings, EmbeddingModelSettings)
    with pytest.raises(ValidationError):
        InferenceModel(
            endpoint_id="endpoint-1",
            display_name="invalid",
            model_name="gpt-4o",
            kind=InferenceModelKind.EMBEDDING,
            settings=ChatModelSettings(),
        )


def test_endpoint_owns_connection_and_binding_owns_only_selection() -> None:
    endpoint = InferenceEndpoint(
        display_name="OpenAI",
        provider=InferenceProvider.OPENAI,
        base_url="https://api.openai.com/v1",
        credential="secret",
    )
    binding = InferenceBinding(
        purpose=InferencePurpose.CHAT,
        model_id="model-1",
        owner_user_id="user-1",
    )

    assert endpoint.credential == "secret"
    assert binding.model_id == "model-1"
    assert "credential" not in InferenceBinding.model_fields


def test_inference_resources_forbid_unknown_fields() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        InferenceEndpoint(
            display_name="OpenAI",
            provider=InferenceProvider.OPENAI,
            base_url="https://api.openai.com/v1",
            credential="secret",
            visibility=ResourceVisibility.GLOBAL,
            created_at=now,
            unknown="forbidden",
        )


def test_resolved_chat_model_projects_adapter_fields_without_copying_secrets_to_model() -> None:
    model = InferenceModel(
        id="chat-1",
        endpoint_id="endpoint-1",
        display_name="Chat",
        model_name="gpt-4o",
        settings=ChatModelSettings(temperature=0.2, max_output_tokens=2048),
    )
    resolved = ResolvedInferenceModel(
        model=model,
        endpoint=InferenceEndpoint(
            id="endpoint-1",
            display_name="OpenAI",
            credential="secret",
        ),
    )

    assert resolved.display_name == "Chat"
    assert resolved.temperature == 0.2
    assert resolved.max_tokens == 2048
    assert resolved.credential == "secret"
    assert "credential" not in resolved.model.model_dump()
