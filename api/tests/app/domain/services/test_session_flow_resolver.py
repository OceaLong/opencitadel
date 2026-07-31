#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.models.codebase import SessionMode
from app.domain.services.session_flow_resolver import FlowKind, SessionFlowResolver


@pytest.mark.parametrize(
    ("mode", "has_kb", "has_codebase", "expected"),
    [
        (SessionMode.ASK, True, False, FlowKind.DOC_ASK),
        (SessionMode.AGENT, True, False, FlowKind.PLANNER_REACT),
        (SessionMode.ASK, False, True, FlowKind.CODE_ASK),
        (SessionMode.AGENT, False, True, FlowKind.PLANNER_REACT),
        (SessionMode.ASK, True, True, FlowKind.HYBRID_ASK),
        (SessionMode.AGENT, True, True, FlowKind.PLANNER_REACT),
        (SessionMode.AGENT, False, False, FlowKind.PLANNER_REACT),
    ],
)
def test_flow_matrix(mode, has_kb, has_codebase, expected):
    """Catches a resource-specific flow overriding the selected session mode."""
    assert (
        SessionFlowResolver.resolve(mode, has_kb, has_codebase).flow_kind == expected
    )
