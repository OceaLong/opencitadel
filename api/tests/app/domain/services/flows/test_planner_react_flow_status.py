#!/usr/bin/env python
# -*- coding: utf-8 -*-
from app.domain.services.flows.base import FlowStatus


def test_flow_status_values():
    assert FlowStatus.IDLE.value == "idle"
    assert FlowStatus.CLARIFYING.value == "clarifying"
    assert FlowStatus.PLANNING.value == "planning"
    assert FlowStatus.EXECUTING.value == "executing"
    assert FlowStatus.COMPLETED.value == "completed"


def test_flow_status_terminal_is_completed():
    terminal = {FlowStatus.COMPLETED}
    assert FlowStatus.COMPLETED in terminal
    assert FlowStatus.EXECUTING not in terminal
