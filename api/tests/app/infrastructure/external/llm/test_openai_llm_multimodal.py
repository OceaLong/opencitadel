from app.domain.models.inference import InferenceCapabilities, InferenceProvider
from app.infrastructure.external.llm.openai_llm import OpenAILLM
from tests.app.infrastructure.external.llm.inference_model_factory import (
    resolved_chat_model,
)


def test_openai_llm_exposes_vision_capability():
    llm = OpenAILLM(
        resolved_chat_model(
            provider=InferenceProvider.OPENAI,
            base_url="https://api.openai.com/v1",
            credential="sk-test",
            model_name="gpt-4o",
            capabilities=InferenceCapabilities(vision=True),
        )
    )
    assert llm.capabilities.vision is True
