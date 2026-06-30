#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging

from app.infrastructure.observability.logging_context import (
    CorrelationContextFilter,
    bind_context,
    configure_structured_logging,
    request_id_var,
    session_id_var,
    task_id_var,
)


def test_bind_context_resets_after_exit():
    session_id_var.set("old")
    with bind_context(session_id="sess-1", task_id="task-1", request_id="req-1"):
        assert session_id_var.get() == "sess-1"
        assert task_id_var.get() == "task-1"
        assert request_id_var.get() == "req-1"
    assert session_id_var.get() == "old"


def test_configure_structured_logging_replaces_filter():
    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.handlers.clear()
    root.addHandler(handler)

    configure_structured_logging()
    first_filters = [f for f in handler.filters if isinstance(f, CorrelationContextFilter)]
    assert len(first_filters) == 1

    configure_structured_logging()
    second_filters = [f for f in handler.filters if isinstance(f, CorrelationContextFilter)]
    assert len(second_filters) == 1
    assert second_filters[0] is not first_filters[0]
