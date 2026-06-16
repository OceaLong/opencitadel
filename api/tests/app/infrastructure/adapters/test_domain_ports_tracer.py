#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.infrastructure.adapters.domain_ports import OtelObservabilityAdapter
from app.infrastructure.observability.agent_tracer import AgentTracer


def test_create_agent_tracer_accepts_agent_name():
    adapter = OtelObservabilityAdapter()
    tracer = adapter.create_agent_tracer("sess-1", "planner_react_flow")
    assert isinstance(tracer, AgentTracer)
    assert tracer._session_id == "sess-1"
    assert tracer._agent_name == "planner_react_flow"
    with tracer.span("test-span"):
        pass
