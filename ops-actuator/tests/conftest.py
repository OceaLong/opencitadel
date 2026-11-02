"""Thread-safe API fakes exercising the real preparation and journal CAS code."""

from __future__ import annotations

import copy
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException
from opencitadel_ops_actuator.actuator import Actuator
from opencitadel_ops_actuator.config import ActuatorSettings, WorkloadTarget
from opencitadel_ops_actuator.contracts import operation_payload_digest
from opencitadel_ops_actuator.k8s_writer import (
    OPERATION_ANNOTATION,
    RESTARTED_AT_ANNOTATION,
    REVISION_ANNOTATION,
    KubernetesWriter,
    WorkloadNotFound,
)
from opencitadel_ops_actuator.operation_journal import ConfigMapJournal


class FakeCore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.fail = False
        self.fail_receipt = False
        self.version = 0

    def _check(self) -> None:
        if self.fail:
            raise ApiException(status=403)

    @staticmethod
    def _object(body: dict[str, Any]) -> Any:
        return SimpleNamespace(
            metadata=SimpleNamespace(resource_version=body["metadata"]["resourceVersion"]),
            data=copy.deepcopy(body["data"]),
        )

    def read_namespaced_config_map(self, name: str, namespace: str, **options) -> Any:
        with self.lock:
            self._check()
            if (namespace, name) not in self.items:
                raise ApiException(status=404)
            return self._object(self.items[namespace, name])

    def create_namespaced_config_map(self, namespace: str, body: dict[str, Any], **options) -> Any:
        with self.lock:
            self._check()
            key = (namespace, body["metadata"]["name"])
            if key in self.items:
                raise ApiException(status=409)
            self.version += 1
            saved = copy.deepcopy(body)
            saved["metadata"]["resourceVersion"] = str(self.version)
            self.items[key] = saved
            return self._object(saved)

    def replace_namespaced_config_map(
        self, name: str, namespace: str, body: dict[str, Any], **options
    ) -> Any:
        with self.lock:
            self._check()
            if self.fail_receipt and '"state":"applied"' in body["data"]["record.json"]:
                raise ApiException(status=503)
            key = (namespace, name)
            current = self.items.get(key)
            if current is None:
                raise ApiException(status=404)
            if current["metadata"]["resourceVersion"] != body["metadata"]["resourceVersion"]:
                raise ApiException(status=409)
            self.version += 1
            saved = copy.deepcopy(body)
            saved["metadata"]["resourceVersion"] = str(self.version)
            self.items[key] = saved
            return self._object(saved)


class FakeKubernetesWriter(KubernetesWriter):
    def __init__(self, *, revisions: list[Any] | None = None) -> None:
        self.core = FakeCore()
        self.lock = threading.Lock()
        self.uid = "uid-api-1"
        self.resource_version = 1
        self.replicas = 3
        self.generation = 1
        self.annotations: dict[str, str] = {}
        self.restart_calls = self.scale_calls = self.rollback_calls = 0
        self.patch_attempts = 0
        self.missing = False
        self.raise_on_mutation: Exception | None = None
        self.lose_response = False
        self.revisions = (
            list(revisions) if revisions is not None else ["template-v1", "template-v2"]
        )
        self.current_template = self.revisions[-1] if self.revisions else "template-v1"
        self.last_patch: dict[str, Any] | None = None

    @property
    def marker(self) -> str | None:
        return self.annotations.get(OPERATION_ANNOTATION)

    def _read(self, namespace: str, name: str, kind: str) -> Any:
        with self.lock:
            if self.missing:
                raise WorkloadNotFound("missing")
            return SimpleNamespace(
                metadata=SimpleNamespace(
                    uid=self.uid,
                    resource_version=str(self.resource_version),
                    generation=self.generation,
                    annotations=dict(self.annotations),
                ),
                spec=SimpleNamespace(replicas=self.replicas),
                status=SimpleNamespace(replicas=self.replicas, ready_replicas=self.replicas),
            )

    def _list_replica_sets(self, namespace: str, deployment_name: str) -> list[Any]:
        return [
            SimpleNamespace(
                metadata=SimpleNamespace(
                    annotations={REVISION_ANNOTATION: str(i + 1)},
                    owner_references=[
                        SimpleNamespace(uid=self.uid, kind="Deployment", name=deployment_name)
                    ],
                ),
                spec=SimpleNamespace(
                    template=template if isinstance(template, dict) else {"version": template}
                ),
            )
            for i, template in enumerate(self.revisions)
        ]

    def _patch(self, namespace: str, name: str, kind: str, body: dict[str, Any]) -> Any:
        with self.lock:
            self.patch_attempts += 1
            if self.raise_on_mutation:
                raise self.raise_on_mutation
            if body["metadata"]["uid"] != self.uid or body["metadata"]["resourceVersion"] != str(
                self.resource_version
            ):
                raise ApiException(status=409)
            self.last_patch = copy.deepcopy(body)
            self.annotations.update(body["metadata"]["annotations"])
            spec = body["spec"]
            if "replicas" in spec:
                self.scale_calls += 1
                self.replicas = spec["replicas"]
            elif RESTARTED_AT_ANNOTATION in spec["template"].get("metadata", {}).get(
                "annotations", {}
            ):
                self.restart_calls += 1
                self.generation += 1
            else:
                self.rollback_calls += 1
                self.current_template = spec["template"].get("version", spec["template"])
            self.resource_version += 1
        if self.lose_response:
            raise ConnectionError("response lost after apply")
        return self._read(namespace, name, kind)


@pytest.fixture
def actuator_settings():
    def make(**overrides: Any) -> ActuatorSettings:
        base: dict[str, Any] = {
            "journal_namespace": "journal-test",
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

    return make


@pytest.fixture
def fake_k8s() -> FakeKubernetesWriter:
    return FakeKubernetesWriter()


@pytest.fixture
def make_actuator(actuator_settings, fake_k8s):
    def make(settings=None):
        cfg = settings or actuator_settings()
        return Actuator(
            cfg,
            k8s_factory=lambda: fake_k8s,
            journal_factory=lambda: ConfigMapJournal(cfg, core=fake_k8s.core),
        )

    return make


@pytest.fixture
def invoke():
    async def call(
        actuator,
        action,
        namespace="demo",
        workload="api",
        kind="deployment",
        key="key-1",
        operation_id=None,
        target_uid="uid-api-1",
        **parameters,
    ):
        operation_id = operation_id or f"operation:{key}"
        digest = operation_payload_digest(
            action, actuator.settings.target_ref, namespace, workload, kind, target_uid, parameters
        )
        return await getattr(actuator, action)(
            namespace=namespace,
            workload=workload,
            kind=kind,
            idempotency_key=key,
            operation_id=operation_id,
            payload_digest=digest,
            target_uid=target_uid,
            **parameters,
        )

    return call
