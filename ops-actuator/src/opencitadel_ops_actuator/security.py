"""Allowlist enforcement for every registered write target.

Structure mirrors ops-collector/src/opencitadel_ops_collector/security.py,
adapted to return the registered WorkloadTarget (kind + replica bounds)
instead of only validating membership, since write actions must know the
registered kind/bounds to enforce KIND_MISMATCH and REPLICAS_OUT_OF_BOUNDS.
"""
from __future__ import annotations

from .config import ActuatorSettings, WorkloadTarget


class TargetDenied(ValueError):
    pass


def require_namespace(settings: ActuatorSettings, namespace: str) -> None:
    if namespace not in settings.allowed_namespaces:
        raise TargetDenied("NAMESPACE_DENIED")


def require_workload(settings: ActuatorSettings, namespace: str, workload_id: str) -> WorkloadTarget:
    require_namespace(settings, namespace)
    configured = settings.allowed_workloads.get(namespace, {})
    target = configured.get(workload_id)
    if target is None:
        raise TargetDenied("TARGET_DENIED")
    return target


def require_replicas_in_bounds(target: WorkloadTarget, replicas: int) -> None:
    if replicas < target.min_replicas or replicas > target.max_replicas:
        raise TargetDenied("REPLICAS_OUT_OF_BOUNDS")
