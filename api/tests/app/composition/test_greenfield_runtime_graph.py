"""The composition graph is context-sized, not a service locator."""

from __future__ import annotations

import inspect

from app.composition.types import ApiRuntime, KernelRuntime


def test_api_runtime_contains_contexts_not_service_inventory() -> None:
    assert tuple(inspect.signature(ApiRuntime).parameters) == (
        "settings",
        "resources",
        "readiness",
        "supervisor",
        "identity",
        "inference",
        "knowledge",
        "kernel",
    )


def test_worker_runtime_contains_the_same_contexts_with_worker_kernel() -> None:
    assert tuple(inspect.signature(KernelRuntime).parameters) == (
        "settings",
        "resources",
        "readiness",
        "supervisor",
        "identity",
        "inference",
        "knowledge",
        "kernel",
    )
