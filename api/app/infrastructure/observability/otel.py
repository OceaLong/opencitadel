#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OpenTelemetry and Prometheus observability setup."""
import logging

from app.application.services.config_provider import get_runtime_config

logger = logging.getLogger(__name__)
_initialized = False


def setup_observability(app=None) -> None:
    global _initialized
    if _initialized:
        return
    observability = get_runtime_config().observability
    if not observability.otel_enabled:
        logger.info("OpenTelemetry disabled (otel_enabled=false in config.yaml)")
        _initialized = True
        return

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OpenTelemetry packages not installed; skipping observability setup")
        _initialized = True
        return

    resource = Resource.create({
        "service.name": observability.otel_service_name,
        "service.version": "1.0.0",
    })
    tracer_provider = TracerProvider(resource=resource)
    if observability.otel_exporter_endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=observability.otel_exporter_endpoint))
        )
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=observability.otel_exporter_endpoint)
        if observability.otel_exporter_endpoint else OTLPMetricExporter()
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            pass

    _initialized = True
    logger.info("OpenTelemetry initialized: service=%s", observability.otel_service_name)


def get_tracer(name: str = "my-manus"):
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpTracer:
    def start_as_current_span(self, name: str, **kwargs):
        from contextlib import nullcontext
        return nullcontext()


_agent_step_counter = None
_llm_token_counter = None


def record_agent_step(agent_name: str, step: str) -> None:
    global _agent_step_counter
    try:
        from opentelemetry import metrics
        if _agent_step_counter is None:
            meter = metrics.get_meter("my-manus.agent")
            _agent_step_counter = meter.create_counter("agent_steps_total")
        _agent_step_counter.add(1, {"agent": agent_name, "step": step})
    except Exception:
        pass


def record_llm_tokens(model: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    global _llm_token_counter
    try:
        from opentelemetry import metrics
        if _llm_token_counter is None:
            meter = metrics.get_meter("my-manus.llm")
            _llm_token_counter = meter.create_counter("llm_tokens_total")
        if prompt_tokens:
            _llm_token_counter.add(prompt_tokens, {"model": model, "type": "prompt"})
        if completion_tokens:
            _llm_token_counter.add(completion_tokens, {"model": model, "type": "completion"})
    except Exception:
        pass


_agent_cancel_counter = None


def record_agent_cancel(session_id: str = "") -> None:
    global _agent_cancel_counter
    try:
        from opentelemetry import metrics
        if _agent_cancel_counter is None:
            meter = metrics.get_meter("my-manus.agent")
            _agent_cancel_counter = meter.create_counter("agent_cancellations_total")
        _agent_cancel_counter.add(1, {"session_id": session_id or "unknown"})
    except Exception:
        pass
