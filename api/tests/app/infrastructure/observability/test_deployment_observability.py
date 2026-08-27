from app.infrastructure.observability import otel
from app.infrastructure.observability.agent_tracer import AgentTracer
from core.config import DeploymentSettings


def test_observability_setup_accepts_restart_bound_settings(monkeypatch) -> None:
    monkeypatch.setattr(otel, "_initialized", False)

    otel.setup_observability(
        settings=DeploymentSettings(otel_enabled=False),
    )

    assert otel._initialized is True


def test_agent_tracer_accepts_restart_bound_settings() -> None:
    tracer = AgentTracer(
        session_id="session-1",
        agent_name="agent-1",
        settings=DeploymentSettings(otel_enabled=False),
    )

    with tracer.span("step"):
        pass
