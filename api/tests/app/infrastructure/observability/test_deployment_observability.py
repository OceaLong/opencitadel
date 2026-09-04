from app.infrastructure.observability.agent_tracer import AgentTracer
from core import otel
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


def test_noop_tracer_yields_dummy_span_supporting_span_api() -> None:
    """When OpenTelemetry is absent, callers still get a span-like object.

    Regression: the old ``_NoOpTracer`` yielded ``None`` (nullcontext), so
    ``with tracer.start_as_current_span(...) as span: span.set_attribute(...)``
    raised ``AttributeError``.
    """

    tracer = otel._NoOpTracer()

    with tracer.start_as_current_span("activity.execute") as span:
        assert span is not None
        # None of these must raise.
        span.set_attribute("opencitadel.run_id", "run-1")
        span.set_attributes({"k": "v"})
        span.record_exception(RuntimeError("boom"))
        span.set_status("error")
        span.add_event("event")
        span.end()
