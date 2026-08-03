"""Narrow read-only Kubernetes adapter. It never requests Secrets or exec APIs."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any


class KubernetesReader:
    def __init__(self) -> None:
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except Exception:
            config.load_kube_config(context=os.getenv("PATROL_DEMO_CONTEXT") or None)
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.batch = client.BatchV1Api()

    @staticmethod
    def _name(item: Any, kind: str) -> str:
        return f"{kind}/{item.metadata.name}"

    def workload_summary(self, namespace: str, workload_ids: list[str], window_seconds: int = 3600) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        allowed = set(workload_ids)
        unavailable = 0
        not_ready: list[str] = []
        workloads: list[dict[str, Any]] = []
        for kind, items in (
            ("deployment", self.apps.list_namespaced_deployment(namespace).items),
            ("statefulset", self.apps.list_namespaced_stateful_set(namespace).items),
            ("daemonset", self.apps.list_namespaced_daemon_set(namespace).items),
        ):
            for item in items:
                ref = self._name(item, kind)
                if allowed and ref not in allowed:
                    continue
                desired = int(getattr(item.status, "replicas", 0) or getattr(item.status, "desired_number_scheduled", 0) or 0)
                ready = int(getattr(item.status, "ready_replicas", 0) or getattr(item.status, "number_ready", 0) or 0)
                missing = max(0, desired - ready)
                unavailable += missing
                if missing:
                    not_ready.append(ref)
                workloads.append({"ref": ref, "desired": desired, "ready": ready, "unavailable": missing})

        pending_pods: list[dict[str, str]] = []
        restart_count = 0
        for pod in self.core.list_namespaced_pod(namespace).items:
            if pod.status.phase == "Pending":
                reason = "Pending"
                for condition in pod.status.conditions or []:
                    if condition.status != "True" and condition.reason:
                        reason = condition.reason
                pending_pods.append({"ref": self._name(pod, "pod"), "reason": reason})
            for status in (pod.status.container_statuses or []):
                last_terminated = getattr(getattr(status, "last_state", None), "terminated", None)
                finished_at = getattr(last_terminated, "finished_at", None)
                if finished_at is None or now - finished_at <= timedelta(seconds=window_seconds):
                    restart_count += int(status.restart_count or 0)
                waiting = getattr(getattr(status, "state", None), "waiting", None)
                if waiting and waiting.reason in {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull"}:
                    pending_pods.append({"ref": self._name(pod, "pod"), "reason": waiting.reason})

        failed_jobs = [self._name(item, "job") for item in self.batch.list_namespaced_job(namespace).items if int(item.status.failed or 0) > 0]
        stale_cronjobs: list[str] = []
        for item in self.batch.list_namespaced_cron_job(namespace).items:
            reference = getattr(item.status, "last_successful_time", None) or item.metadata.creation_timestamp
            annotated = (item.metadata.annotations or {}).get("ops.opencitadel.io/last-success-at")
            if annotated:
                try:
                    reference = datetime.fromisoformat(annotated.replace("Z", "+00:00"))
                except ValueError:
                    pass
            if reference and now - reference > timedelta(hours=24):
                stale_cronjobs.append(self._name(item, "cronjob"))
        node_pressures: list[dict[str, str]] = []
        for node in self.core.list_node().items:
            for condition in node.status.conditions or []:
                if condition.type in {"DiskPressure", "MemoryPressure", "PIDPressure"} and condition.status == "True":
                    node_pressures.append({"ref": self._name(node, "node"), "condition": condition.type})
        return {
            "unavailable_replicas": unavailable,
            "not_ready_workloads": sorted(not_ready),
            "workloads": sorted(workloads, key=lambda item: item["ref"]),
            "restart_count_1h": restart_count,
            "pending_pods": sorted(
                ({"ref": ref, "reason": reason} for ref, reason in {(item["ref"], item["reason"]) for item in pending_pods}),
                key=lambda item: (item["ref"], item["reason"]),
            ),
            "failed_jobs": sorted(failed_jobs),
            "stale_cronjobs": sorted(stale_cronjobs),
            "node_pressures": sorted(node_pressures, key=lambda item: (item["ref"], item["condition"])),
        }

    def recent_events(self, namespace: str, since_seconds: int, limit: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        rows: list[dict[str, Any]] = []
        for item in self.core.list_namespaced_event(namespace).items:
            timestamp = item.last_timestamp or item.event_time or item.metadata.creation_timestamp
            if timestamp and timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            if timestamp and (now - timestamp).total_seconds() > since_seconds:
                continue
            if item.type != "Warning":
                continue
            ref = f"{(item.involved_object.kind or 'object').lower()}/{item.involved_object.name}"
            rows.append({"reason": item.reason or "Unknown", "resource_ref": ref, "count": int(item.count or 1), "last_seen": timestamp.isoformat() if timestamp else None, "message": item.message or ""})
        rows.sort(key=lambda value: (value["reason"], value["resource_ref"]))
        return {"warning_events": rows[:limit], "high_risk_count": sum(1 for item in rows if item["reason"] in {"FailedMount", "FailedScheduling", "FailedAttachVolume", "BackOff"})}

    def pod_logs(self, namespace: str, pod: str, container: str | None, tail_lines: int, since_seconds: int) -> dict[str, Any]:
        text = self.core.read_namespaced_pod_log(
            pod,
            namespace,
            container=container,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
            timestamps=True,
            limit_bytes=32768,
        )
        return {"pod": pod, "container": container, "log_excerpt": text}
