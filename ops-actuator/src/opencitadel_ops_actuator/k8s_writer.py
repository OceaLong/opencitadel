"""Kubernetes preparation and fixed resourceVersion-conditional main-resource patches.

The journal owns key deduplication. Each mutation atomically carries its operation
ID and payload digest together with the business spec. Recovery may submit only
this persisted patch; a conflict never causes a read-and-rebase. Workload reads
are limited to registered targets and owned ReplicaSets; Secrets are never read.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from kubernetes.client.exceptions import ApiException

RESTARTED_AT_ANNOTATION = "opencitadel.io/restarted-at"
OPERATION_ANNOTATION = "opencitadel.io/operation-id"
PAYLOAD_ANNOTATION = "opencitadel.io/payload-digest"
REVISION_ANNOTATION = "deployment.kubernetes.io/revision"
KUBERNETES_TIMEOUT = (5, 30)


class NoRollbackRevision(Exception):
    """Raised when a Deployment has no previous ReplicaSet revision to roll back to."""


class WorkloadNotFound(Exception):
    """Raised when a registered workload no longer exists in the cluster."""


def load_incluster_config() -> None:
    from kubernetes import config
    from kubernetes.config.config_exception import ConfigException

    try:
        config.load_incluster_config()
    except (ConfigException, OSError, RuntimeError, ValueError):
        context = os.getenv("PATROL_DEMO_CONTEXT")
        if not context:
            raise RuntimeError(
                "explicit PATROL_DEMO_CONTEXT is required outside a cluster"
            ) from None
        config.load_kube_config(context=context)


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
                return self.apps.read_namespaced_deployment(
                    name, namespace, _request_timeout=KUBERNETES_TIMEOUT
                )
            return self.apps.read_namespaced_stateful_set(
                name, namespace, _request_timeout=KUBERNETES_TIMEOUT
            )
        except ApiException as exc:
            if getattr(exc, "status", None) == 404:
                raise WorkloadNotFound(f"{kind}/{name} not found in {namespace}") from exc
            raise

    def _patch(self, namespace: str, name: str, kind: str, body: dict[str, Any]) -> Any:
        options = {
            "_content_type": "application/strategic-merge-patch+json",
            "_request_timeout": KUBERNETES_TIMEOUT,
        }
        if kind == "deployment":
            return self.apps.patch_namespaced_deployment(name, namespace, body, **options)
        return self.apps.patch_namespaced_stateful_set(name, namespace, body, **options)

    def _list_replica_sets(self, namespace: str, deployment_name: str) -> list[Any]:
        items = self.apps.list_namespaced_replica_set(
            namespace, _request_timeout=KUBERNETES_TIMEOUT
        ).items
        return [
            item
            for item in items
            if any(
                getattr(ref, "kind", None) == "Deployment"
                and getattr(ref, "name", None) == deployment_name
                for ref in (item.metadata.owner_references or [])
            )
        ]

    @staticmethod
    def _object_annotations(item: Any) -> dict[str, str]:
        return dict(getattr(item.metadata, "annotations", None) or {})

    @staticmethod
    def _snapshot(item: Any) -> dict[str, Any]:
        status = getattr(item, "status", None)
        return {
            "uid": str(item.metadata.uid),
            "resource_version": str(item.metadata.resource_version),
            "replicas": int(getattr(item.spec, "replicas", 0) or 0),
            "ready_replicas": int(getattr(status, "ready_replicas", 0) or 0),
            "generation": int(item.metadata.generation or 0),
        }

    def observe(self, target: dict[str, str]) -> dict[str, Any]:
        item = self._read(target["namespace"], target["workload"], target["kind"])
        return {"snapshot": self._snapshot(item), "annotations": self._object_annotations(item)}

    def prepare(
        self,
        action: str,
        target: dict[str, str],
        operation_id: str,
        payload_digest: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Read once and return a serializable patch; recovery never calls this again."""
        item = self._read(target["namespace"], target["workload"], target["kind"])
        before = self._snapshot(item)
        if before["uid"] != target["uid"]:
            raise ValueError("TARGET_UID_MISMATCH")
        if not before["resource_version"] or before["resource_version"] == "None":
            raise ValueError("target has no resourceVersion")
        body: dict[str, Any] = {
            "metadata": {
                "uid": target["uid"],
                "resourceVersion": before["resource_version"],
                "annotations": {
                    OPERATION_ANNOTATION: operation_id,
                    PAYLOAD_ANNOTATION: payload_digest,
                },
            }
        }
        hint: dict[str, Any] = {}
        if action == "restart_workload":
            hint["restarted_at"] = datetime.now(UTC).isoformat()
            body["spec"] = {
                "template": {
                    "metadata": {
                        "annotations": {
                            RESTARTED_AT_ANNOTATION: hint["restarted_at"],
                        }
                    }
                }
            }
        elif action == "scale_workload":
            body["spec"] = {"replicas": parameters["replicas"]}
        elif action == "rollback_workload":
            sets = self._list_replica_sets(target["namespace"], target["workload"])
            sets = [
                rs
                for rs in sets
                if any(
                    str(getattr(ref, "uid", "")) == target["uid"]
                    for ref in (rs.metadata.owner_references or [])
                )
            ]
            ranked = sorted(
                sets,
                key=lambda rs: (
                    -int(self._object_annotations(rs).get(REVISION_ANNOTATION, "0") or "0")
                ),
            )
            if len(ranked) < 2:
                raise NoRollbackRevision("NO_ROLLBACK_REVISION")
            previous = ranked[1]
            # Kubernetes' serializer preserves wire camelCase. Model.to_dict()
            # produces snake_case and is not a valid pod-template wire payload.
            from kubernetes import client

            template = client.ApiClient().sanitize_for_serialization(previous.spec.template)
            # Replace the whole template; merge alone leaves fields introduced
            # after the selected revision and is not an actual rollback.
            body["spec"] = {"template": {**template, "$patch": "replace"}}
            hint["rolled_back_to_revision"] = self._object_annotations(previous).get(
                REVISION_ANNOTATION
            )
        else:
            raise ValueError("unsupported action")
        return {"patch": body, "before": before, "after_hint": hint}

    def apply_prepared(self, target: dict[str, str], patch: dict[str, Any]) -> dict[str, Any]:
        item = self._patch(target["namespace"], target["workload"], target["kind"], patch)
        return self._snapshot(item)
