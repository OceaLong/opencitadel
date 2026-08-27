"""Shared fakes and fixtures for opencitadel-ops-actuator tests.

FakeKubernetesWriter is a business-level fake (restart/scale/rollback/
read_marker/snapshot) injected via Actuator's k8s_factory, mirroring the
FakeKubernetes pattern in ops-collector/tests/test_k8s_probes.py. It never
touches the real `kubernetes` client library.

All three mutating methods funnel through _mutate_main_resource(), the same
way the real KubernetesWriter now funnels restart/scale/rollback through a
single _patch() (main-resource merge patch) instead of the apps/v1 /scale
subresource -- see k8s_writer.py's module docstring for why /scale can't
carry the idempotency annotation on a real cluster. Routing the fake through
one shared method keeps it from masking that real-vs-fake divergence again:
there is no separate "scale subresource" code path here to fake out.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from opencitadel_ops_actuator.config import ActuatorSettings, WorkloadTarget
from opencitadel_ops_actuator.k8s_writer import NoRollbackRevision, WorkloadNotFound


class FakeKubernetesWriter:
    def __init__(self, *, revisions: list[str] | None = None) -> None:
        self.marker: str | None = None
        self.replicas = 3
        self.generation = 1
        self.restart_calls = 0
        self.scale_calls = 0
        self.rollback_calls = 0
        self.missing = False
        # When set, the next call into _mutate_main_resource raises this
        # exception instead of mutating state -- used to prove a transient
        # k8s failure is attempted exactly once and never retried.
        self.raise_on_mutation: Exception | None = None
        self.revisions = (
            list(revisions) if revisions is not None else ["template-v1", "template-v2"]
        )
        self.current_template = self.revisions[-1] if self.revisions else "template-v1"

    def _assert_present(self) -> None:
        if self.missing:
            raise WorkloadNotFound("workload not found")

    def read_marker(self, namespace: str, name: str, kind: str) -> str | None:
        self._assert_present()
        return self.marker

    def snapshot(self, namespace: str, name: str, kind: str) -> dict[str, Any]:
        self._assert_present()
        return {
            "replicas": self.replicas,
            "ready_replicas": self.replicas,
            "generation": self.generation,
            "template": self.current_template,
        }

    def _mutate_main_resource(
        self, namespace: str, name: str, kind: str, idempotency_key: str, apply: Callable[[], None]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Single main-resource "patch": snapshot before, optionally raise
        (simulating a failed/never-retried k8s call), else apply the spec
        change and the idempotency marker atomically, snapshot after."""
        self._assert_present()
        before = self.snapshot(namespace, name, kind)
        if self.raise_on_mutation is not None:
            raise self.raise_on_mutation
        apply()
        self.marker = idempotency_key
        after = self.snapshot(namespace, name, kind)
        return before, after

    def restart(self, namespace: str, name: str, kind: str, idempotency_key: str) -> dict[str, Any]:
        self.restart_calls += 1

        def apply() -> None:
            self.generation += 1

        before, after = self._mutate_main_resource(namespace, name, kind, idempotency_key, apply)
        after["restarted_at"] = "2026-08-04T00:00:00+00:00"
        return {"before": before, "after": after}

    def scale(
        self, namespace: str, name: str, kind: str, replicas: int, idempotency_key: str
    ) -> dict[str, Any]:
        self.scale_calls += 1

        def apply() -> None:
            self.replicas = replicas

        before, after = self._mutate_main_resource(namespace, name, kind, idempotency_key, apply)
        return {"before": before, "after": after}

    def rollback(self, namespace: str, name: str, idempotency_key: str) -> dict[str, Any]:
        self._assert_present()
        if len(self.revisions) < 2:
            raise NoRollbackRevision("NO_ROLLBACK_REVISION")
        self.rollback_calls += 1
        target_template = self.revisions[-2]

        def apply() -> None:
            self.current_template = target_template

        before, after = self._mutate_main_resource(
            namespace, name, "deployment", idempotency_key, apply
        )
        after["rolled_back_to_revision"] = None
        return {"before": before, "after": after}


@pytest.fixture
def actuator_settings():
    def _make(**overrides: Any) -> ActuatorSettings:
        base: dict[str, Any] = {
            "allowed_namespaces": ["demo"],
            "allowed_workloads": {
                "demo": {
                    "api": WorkloadTarget(kind="deployment", min_replicas=1, max_replicas=10),
                    "cache": WorkloadTarget(kind="statefulset", min_replicas=1, max_replicas=5),
                }
            },
        }
        base.update(overrides)
        return ActuatorSettings(**base)

    return _make


@pytest.fixture
def fake_k8s() -> FakeKubernetesWriter:
    return FakeKubernetesWriter()
