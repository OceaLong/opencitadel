"""OpenTelemetry-backed agent tracing helpers."""

from core.config import DeploymentSettings


class AgentTracer:
    """Lightweight agent step tracer backed by OpenTelemetry spans."""

    def __init__(
        self,
        session_id: str,
        agent_name: str = "",
        *,
        settings: DeploymentSettings,
    ) -> None:
        self._session_id = session_id
        self._agent_name = agent_name
        self._enabled = settings.otel_enabled
        self._tracer = None
        if self._enabled:
            try:
                from app.infrastructure.observability.otel import get_tracer

                self._tracer = get_tracer("opencitadel.agent")
            except (OSError, RuntimeError, ValueError):
                self._enabled = False

    def span(self, name: str, attributes: dict | None = None):
        if not self._enabled or not self._tracer:
            from contextlib import nullcontext

            return nullcontext()
        attrs = {
            "session_id": self._session_id,
            "agent_name": self._agent_name,
            **(attributes or {}),
        }
        return self._tracer.start_as_current_span(name, attributes=attrs)
