#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prometheus metrics for governance observability (Phase A / Task 1).

Defensive try-import style mirroring ``admission_metrics.py``: when
``prometheus_client`` is unavailable every metric object is ``None`` and the
``record_*``/``observe_*`` functions become silent no-ops so callers never
need to guard on availability themselves.
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram
except ImportError:
    Counter = None  # type: ignore
    Histogram = None  # type: ignore

if Counter is not None:
    GOVERNANCE_APPROVAL_BATCHES = Counter(
        "governance_approval_batches_total",
        "Tool approval batches reaching a terminal outcome",
        ["outcome"],
    )
    GOVERNANCE_APPROVAL_DECISION_SECONDS = Histogram(
        "governance_approval_decision_seconds",
        "Time from approval batch creation to decision",
        buckets=(1.0, 5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0),
    )
    GOVERNANCE_GATE_HITS = Counter(
        "governance_gate_hits_total",
        "Tool calls flagged by the HITL gate policy",
        ["gate"],
    )
    GOVERNANCE_POLICY_DENIALS = Counter(
        "governance_policy_denials_total",
        "Tool calls denied by capability policy, by enforcement layer",
        ["layer", "tool"],
    )
    GOVERNANCE_TOOL_EXECUTIONS = Counter(
        "governance_tool_executions_total",
        "Governed tool call executions, by outcome status",
        ["tool", "status"],
    )
    GOVERNANCE_TOOL_EXECUTION_SECONDS = Histogram(
        "governance_tool_execution_seconds",
        "Governed tool call execution latency",
        ["tool"],
        buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    )
    GOVERNANCE_REMEDIATION_TRANSITIONS = Counter(
        "governance_remediation_transitions_total",
        "Ops Patrol remediation state transitions",
        ["to_status"],
    )
    GOVERNANCE_AUDIT_CHAIN_VERIFICATIONS = Counter(
        "governance_audit_chain_verifications_total",
        "Audit hash-chain verification runs, by result",
        ["result"],
    )
else:
    GOVERNANCE_APPROVAL_BATCHES = None
    GOVERNANCE_APPROVAL_DECISION_SECONDS = None
    GOVERNANCE_GATE_HITS = None
    GOVERNANCE_POLICY_DENIALS = None
    GOVERNANCE_TOOL_EXECUTIONS = None
    GOVERNANCE_TOOL_EXECUTION_SECONDS = None
    GOVERNANCE_REMEDIATION_TRANSITIONS = None
    GOVERNANCE_AUDIT_CHAIN_VERIFICATIONS = None


def record_approval_batch_outcome(outcome: str) -> None:
    """outcome: approved|rejected|expired|consumed"""
    if GOVERNANCE_APPROVAL_BATCHES is not None:
        GOVERNANCE_APPROVAL_BATCHES.labels(outcome=outcome).inc()


def observe_approval_decision_seconds(seconds: float) -> None:
    if GOVERNANCE_APPROVAL_DECISION_SECONDS is not None and seconds >= 0:
        GOVERNANCE_APPROVAL_DECISION_SECONDS.observe(seconds)


def record_gate_hit(gate: str) -> None:
    if GOVERNANCE_GATE_HITS is not None:
        GOVERNANCE_GATE_HITS.labels(gate=gate).inc()


def record_policy_denial(layer: str, tool: str) -> None:
    """layer: assembly|exposure|execution"""
    if GOVERNANCE_POLICY_DENIALS is not None:
        GOVERNANCE_POLICY_DENIALS.labels(layer=layer, tool=tool).inc()


def record_tool_execution(
    tool: str,
    status: str,
    seconds: float | None,
) -> None:
    """status: ok|error|denied"""
    if GOVERNANCE_TOOL_EXECUTIONS is not None:
        GOVERNANCE_TOOL_EXECUTIONS.labels(tool=tool, status=status).inc()
    if (
        GOVERNANCE_TOOL_EXECUTION_SECONDS is not None
        and seconds is not None
        and seconds >= 0
    ):
        GOVERNANCE_TOOL_EXECUTION_SECONDS.labels(tool=tool).observe(seconds)


def record_remediation_transition(to_status: str) -> None:
    """to_status: proposed|executing|executed|verified|failed|cancelled"""
    if GOVERNANCE_REMEDIATION_TRANSITIONS is not None:
        GOVERNANCE_REMEDIATION_TRANSITIONS.labels(to_status=to_status).inc()


def record_chain_verification(result: str) -> None:
    """result: intact|broken"""
    if GOVERNANCE_AUDIT_CHAIN_VERIFICATIONS is not None:
        GOVERNANCE_AUDIT_CHAIN_VERIFICATIONS.labels(result=result).inc()
