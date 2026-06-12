#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prometheus metrics for sandbox admission and task leases."""
from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge
except ImportError:
    Counter = None  # type: ignore
    Gauge = None  # type: ignore

if Counter is not None:
    SANDBOX_QUOTA_INUSE = Gauge(
        "sandbox_quota_inuse",
        "Sandbox quota in use on this node",
        ["node_id"],
    )
    SANDBOX_ADMISSION_REJECTED = Counter(
        "sandbox_admission_rejected_total",
        "Sandbox admission rejections",
        ["reason"],
    )
    TASK_LEASE_CONFLICTS = Counter(
        "task_lease_conflicts_total",
        "Task execution lease conflicts (duplicate run prevented)",
    )
    SANDBOX_RECLAIMED = Counter(
        "sandbox_reclaimed_total",
        "Sandboxes reclaimed by maintenance",
        ["reason"],
    )
else:
    SANDBOX_QUOTA_INUSE = None
    SANDBOX_ADMISSION_REJECTED = None
    TASK_LEASE_CONFLICTS = None
    SANDBOX_RECLAIMED = None


def record_admission_rejected(reason: str) -> None:
    if SANDBOX_ADMISSION_REJECTED is not None:
        SANDBOX_ADMISSION_REJECTED.labels(reason=reason).inc()


def record_task_lease_conflict() -> None:
    if TASK_LEASE_CONFLICTS is not None:
        TASK_LEASE_CONFLICTS.inc()


def record_sandbox_reclaimed(reason: str, count: int = 1) -> None:
    if SANDBOX_RECLAIMED is not None and count > 0:
        SANDBOX_RECLAIMED.labels(reason=reason).inc(count)


def set_quota_inuse(node_id: str, value: int) -> None:
    if SANDBOX_QUOTA_INUSE is not None:
        SANDBOX_QUOTA_INUSE.labels(node_id=node_id).set(value)
