#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""HITL gate resume regression tests."""
import pytest

from app.domain.utils.hitl import (
    PLAN_APPROVAL_PHASE,
    TAKEOVER_PHASE,
    TOOL_APPROVAL_PHASE,
    merge_pending_metadata,
    parse_gate_action,
)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("approve", "approve"),
        ("approve_with_edits", "approve_with_edits"),
        ("approve_same", "approve_same"),
        ("reject: no", "reject"),
        ("takeover", "takeover"),
        ("skip", "skip"),
        ("hello", "unknown"),
    ],
)
def test_parse_gate_action_cases(message, expected):
    action, _ = parse_gate_action(message)
    assert action == expected


def test_merge_pending_metadata_preserves_keys():
    merged = merge_pending_metadata({"plan": {"title": "x"}}, {"approved_tools": ["write_file"]})
    assert merged["plan"]["title"] == "x"
    assert merged["approved_tools"] == ["write_file"]


def test_hitl_phase_constants():
    assert PLAN_APPROVAL_PHASE == "plan_approval"
    assert TOOL_APPROVAL_PHASE == "tool_approval"
    assert TAKEOVER_PHASE == "takeover"
