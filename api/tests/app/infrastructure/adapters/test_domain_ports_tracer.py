from app.infrastructure.observability.agent_tracer import AgentTracer
from app.infrastructure.observability.otel_adapter import OtelObservabilityAdapter
from core.config import DeploymentSettings


def test_create_agent_tracer_accepts_agent_name():
    adapter = OtelObservabilityAdapter(DeploymentSettings(otel_enabled=False))
    tracer = adapter.create_agent_tracer("sess-1", "planner_react_flow")
    assert isinstance(tracer, AgentTracer)
    assert tracer._session_id == "sess-1"
    assert tracer._agent_name == "planner_react_flow"
    with tracer.span("test-span"):
        pass
