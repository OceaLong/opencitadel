import logging

from app.domain.external.llm import LLM
from app.domain.models.inference import ResolvedInferenceModel
from app.infrastructure.external.inference.registry import provider_spec

logger = logging.getLogger(__name__)


class LLMFactory:
    """LLM工厂，根据模型配置创建对应Provider实现"""

    @staticmethod
    def create(model: ResolvedInferenceModel, thinking_enabled: bool = False) -> LLM:
        factory = provider_spec(model.provider).chat_factory
        if factory is None:
            raise ValueError(f"Provider 不支持 Chat 推理: {model.provider.value}")
        return factory(model, thinking_enabled=thinking_enabled)
