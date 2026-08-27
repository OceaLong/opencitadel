from typing import Any

from app.domain.models.inference import (
    ChatModelSettings,
    InferenceCapabilities,
    InferenceEndpoint,
    InferenceModel,
    InferenceProvider,
    ResolvedInferenceModel,
)


def resolved_chat_model(
    *,
    provider: InferenceProvider = InferenceProvider.OPENAI,
    base_url: str = "https://example.com/v1",
    credential: str = "sk-test",
    model_name: str = "test-model",
    display_name: str = "test",
    capabilities: InferenceCapabilities | dict[str, Any] | None = None,
    extra_params: dict[str, Any] | None = None,
    temperature: float = 0.7,
    max_output_tokens: int = 8192,
) -> ResolvedInferenceModel:
    return ResolvedInferenceModel(
        endpoint=InferenceEndpoint(
            provider=provider,
            base_url=base_url,
            credential=credential,
        ),
        model=InferenceModel(
            display_name=display_name,
            model_name=model_name,
            settings=ChatModelSettings(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            ),
            capabilities=capabilities or InferenceCapabilities(),
            extra_params=extra_params or {},
        ),
    )
