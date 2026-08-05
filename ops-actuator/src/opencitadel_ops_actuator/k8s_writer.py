"""Narrow write-only Kubernetes adapter for the three registered actions.

In-cluster/kubeconfig loading mirrors
ops-collector/src/opencitadel_ops_collector/k8s_client.py:13-16. Unlike the
Collector's KubernetesReader (CoreV1/AppsV1/BatchV1, read-only), this
adapter touches only AppsV1 and only ever issues get/list/patch calls
(never list on Secrets, never exec/attach, never create/delete). Every
mutating call carries the caller-supplied idempotency key so a duplicate
request patches the same annotation value instead of re-triggering a
rollout or a redundant scale/rollback.

All three actions (restart/scale/rollback) patch the *main* Deployment or
StatefulSet resource in a single merge patch that carries both the action's
spec change and the idempotency annotation atomically. scale() deliberately
does NOT use the apps/v1 `/scale` subresource: that subresource's Scale
object only round-trips `spec.replicas` on the built-in workload types --
`metadata.annotations` patched through it is a read-only projection and is
not persisted back to the parent Deployment/StatefulSet, which would make
the idempotency key silently fail to stick on a real cluster (it would
still appear to succeed in a naive fake). Patching the main resource keeps
the atomicity guarantee real.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


IDEMPOTENCY_ANNOTATION = "opencitadel.io/remediation-key"
RESTARTED_AT_ANNOTATION = "opencitadel.io/restarted-at"
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"


class NoRollbackRevision(Exception):
    """Raised when a Deployment has no previous ReplicaSet revision to roll back to."""


class WorkloadNotFound(Exception):
    """Raised when a registered workload no longer exists in the cluster."""


def load_incluster_config() -> None:
    from kubernetes import config

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config(context=os.getenv("PATROL_DEMO_CONTEXT") or None)


class KubernetesWriter:
    """Real Kubernetes adapter. Exercised only via integration-marked tests against a disposable cluster;
    unit tests inject a business-level fake exposing the same method surface (see tests/conftest.py),
    mirroring how ops-collector's KubernetesReader is only unit-tested through FakeKubernetes.
    """

    def __init__(self) -> None:
        from kubernetes import client

        load_incluster_config()
        self.apps = client.AppsV1Api()

    # -- low-level dispatch -------------------------------------------------
    def _read(self, namespace: str, name: str, kind: str) -> Any:
        try:
            if kind == "deployment":
                return self.apps.read_namespaced_deployment(name, namespace)
            return self.apps.read_namespaced_stateful_set(name, namespace)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                raise WorkloadNotFound(f"{kind}/{name} not found in {namespace}") from exc
            raise

    def _patch(self, namespace: str, name: str, kind: str, body: dict[str, Any]) -> Any:
        if kind == "deployment":
            return self.apps.patch_namespaced_deployment(name, namespace, body)
        return self.apps.patch_namespaced_stateful_set(name, namespace, body)

    def _list_replica_sets(self, namespace: str, deployment_name: str) -> list[Any]:
        items = self.apps.list_namespaced_replica_set(namespace).items
        return [
            item
            for item in items
            if any(
                getattr(ref, "kind", None) == "Deployment" and getattr(ref, "name", None) == deployment_name
                for ref in (item.metadata.owner_references or [])
            )
        ]

    @staticmethod
    def _object_annotations(item: Any) -> dict[str, str]:
        return dict(getattr(item.metadata, "annotations", None) or {})

    @staticmethod
    def _snapshot(item: Any) -> dict[str, Any]:
        status = item.status
        return {
            "replicas": int(getattr(status, "replicas", 0) or 0),
            "ready_replicas": int(getattr(status, "ready_replicas", 0) or 0),
            "generation": int(item.metadata.generation or 0),
        }

    # -- business-level surface used by actuator.py --------------------------
    def read_marker(self, namespace: str, name: str, kind: str) -> str | None:
        item = self._read(namespace, name, kind)
        return self._object_annotations(item).get(IDEMPOTENCY_ANNOTATION)

    def snapshot(self, namespace: str, name: str, kind: str) -> dict[str, Any]:
        return self._snapshot(self._read(namespace, name, kind))

    def restart(self, namespace: str, name: str, kind: str, idempotency_key: str) -> dict[str, Any]:
        before = self._snapshot(self._read(namespace, name, kind))
        restarted_at = datetime.now(timezone.utc).isoformat()
        body = {
            "metadata": {"annotations": {IDEMPOTENCY_ANNOTATION: idempotency_key}},
            "spec": {"template": {"metadata": {"annotations": {RESTARTED_AT_ANNOTATION: restarted_at}}}},
        }
        after_item = self._patch(namespace, name, kind, body)
        after = self._snapshot(after_item)
        after["restarted_at"] = restarted_at
        return {"before": before, "after": after}

    def scale(self, namespace: str, name: str, kind: str, replicas: int, idempotency_key: str) -> dict[str, Any]:
        before_item = self._read(namespace, name, kind)
        before = {"replicas": int(getattr(before_item.spec, "replicas", 0) or 0)}
        body = {
            "spec": {"replicas": replicas},
            "metadata": {"annotations": {IDEMPOTENCY_ANNOTATION: idempotency_key}},
        }
        # Main-resource patch, not the /scale subresource -- see module
        # docstring for why the subresource cannot carry the idempotency
        # annotation on real clusters.
        after_item = self._patch(namespace, name, kind, body)
        after = {"replicas": int(getattr(getattr(after_item, "spec", None), "replicas", replicas) or replicas)}
        return {"before": before, "after": after}

    def rollback(self, namespace: str, name: str, idempotency_key: str) -> dict[str, Any]:
        deployment = self._read(namespace, name, "deployment")
        before = self._snapshot(deployment)
        replica_sets = self._list_replica_sets(namespace, name)
        ranked = sorted(
            replica_sets,
            key=lambda rs: -int(self._object_annotations(rs).get(REVISION_ANNOTATION, "0") or "0"),
        )
        if len(ranked) < 2:
            raise NoRollbackRevision("NO_ROLLBACK_REVISION")
        target_rs = ranked[1]
        template = target_rs.spec.template
        body = {
            "metadata": {"annotations": {IDEMPOTENCY_ANNOTATION: idempotency_key}},
            "spec": {"template": template.to_dict() if hasattr(template, "to_dict") else template},
        }
        after_item = self._patch(namespace, name, "deployment", body)
        after = self._snapshot(after_item)
        after["rolled_back_to_revision"] = self._object_annotations(target_rs).get(REVISION_ANNOTATION)
        return {"before": before, "after": after}
