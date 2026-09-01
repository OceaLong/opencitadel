from app.domain.external.observability import ObservabilityPort
from app.infrastructure.observability.agent_tracer import AgentTracer
from app.observability.otel import (
    record_agent_cancel,
    record_agent_step,
    record_llm_tokens,
)
from core.config import DeploymentSettings


class OtelObservabilityAdapter(ObservabilityPort):
    def __init__(self, settings: DeploymentSettings) -> None:
        self._settings = settings

    def record_agent_cancel(self, session_id: str) -> None:
        record_agent_cancel(session_id)

    def record_llm_tokens(
        self,
        model: str,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> None:
        record_llm_tokens(
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    def record_agent_step(self, agent_name: str, step: str) -> None:
        record_agent_step(agent_name, step)

    def create_agent_tracer(self, session_id: str, agent_name: str) -> AgentTracer:
        return AgentTracer(
            session_id=session_id,
            agent_name=agent_name,
            settings=self._settings,
        )
