"""Bounded write-action orchestration behind the fixed contract.

Envelope-building shape (semaphore-bounded concurrency, redact, bound,
Envelope construction, exception-to-error-code mapping) mirrors
ops-collector/src/opencitadel_ops_collector/collector.py:49-92. Unlike the
Collector, write actions here NEVER retry: a transient K8S_ERROR is
returned immediately on the first failure. Retry decisions belong to the
upstream approval/remediation chain, not this service -- silently retrying
a write (restart/scale/rollback) risks a double-apply that the caller did
not approve.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from .config import ActuatorSettings
from .contracts import ActuatorEnvelope, ActuatorErrorCode, evidence_for
from .k8s_writer import KubernetesWriter, NoRollbackRevision, WorkloadNotFound
from .limits import bound
from .redaction import redact
from .security import TargetDenied, require_replicas_in_bounds, require_workload


_DENIAL_CODES = {
    "NAMESPACE_DENIED": ActuatorErrorCode.NAMESPACE_DENIED,
    "TARGET_DENIED": ActuatorErrorCode.TARGET_DENIED,
    "REPLICAS_OUT_OF_BOUNDS": ActuatorErrorCode.REPLICAS_OUT_OF_BOUNDS,
}


class _ActionFailure(Exception):
    def __init__(self, code: ActuatorErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class Actuator:
    def __init__(self, settings: ActuatorSettings, *, k8s_factory: Callable[[], Any] = KubernetesWriter) -> None:
        self.settings = settings
        self._k8s_factory = k8s_factory
        self._semaphore = asyncio.Semaphore(settings.concurrency)

    @staticmethod
    def _require_key(idempotency_key: str) -> None:
        if not idempotency_key or not idempotency_key.strip():
            raise _ActionFailure(ActuatorErrorCode.IDEMPOTENCY_KEY_MISSING, "idempotency_key is required")

    def _bound_or_none(self, value: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
        if value is None:
            return None, []
        return bound(redact(value), self.settings)

    async def _execute(self, action: str, operation: Callable[[], tuple[str, Any, Any]], data: dict[str, Any]) -> dict[str, Any]:
        safe_data = redact(data)
        try:
            async with self._semaphore:
                outcome = operation()
                if hasattr(outcome, "__await__"):
                    outcome = await outcome
            action_outcome, before, after = outcome
            bounded_data, data_warnings = bound(safe_data, self.settings)
            bounded_before, before_warnings = self._bound_or_none(before)
            bounded_after, after_warnings = self._bound_or_none(after)
            warnings = sorted(set(data_warnings) | set(before_warnings) | set(after_warnings))
            result = ActuatorEnvelope(
                target_ref=self.settings.target_ref,
                action=action,
                action_outcome=action_outcome,
                before=bounded_before,
                after=bounded_after,
                data=bounded_data,
                evidence=evidence_for(self.settings.target_ref, bounded_data, ["summary", "before_after_snapshot"]),
                warnings=warnings,
            )
        except TargetDenied as exc:
            code = _DENIAL_CODES.get(str(exc), ActuatorErrorCode.TARGET_DENIED)
            result = self._failed(action, safe_data, code, str(exc))
        except _ActionFailure as exc:
            result = self._failed(action, safe_data, exc.code, str(exc))
        except NoRollbackRevision as exc:
            result = self._failed(action, safe_data, ActuatorErrorCode.NO_ROLLBACK_REVISION, str(exc))
        except WorkloadNotFound as exc:
            result = self._failed(action, safe_data, ActuatorErrorCode.TARGET_NOT_FOUND, str(exc))
        except ValueError as exc:
            code = ActuatorErrorCode.OUTPUT_TOO_LARGE if str(exc) == "OUTPUT_TOO_LARGE" else ActuatorErrorCode.INTERNAL
            result = self._failed(action, safe_data, code, str(exc))
        except TimeoutError as exc:
            result = self._failed(action, safe_data, ActuatorErrorCode.TIMEOUT, str(exc))
        except Exception as exc:  # noqa: BLE001 - fail-closed boundary, never retried
            result = self._failed(action, safe_data, ActuatorErrorCode.K8S_ERROR, redact(str(exc)))
        return result.model_dump(mode="json")

    def _failed(self, action: str, data: dict[str, Any], code: ActuatorErrorCode, message: str) -> ActuatorEnvelope:
        return ActuatorEnvelope(
            target_ref=self.settings.target_ref,
            action=action,
            action_outcome="failed",
            data=data,
            error_code=code,
            error_message=message[:2000],
        )

    async def restart_workload(self, namespace: str, workload: str, kind: str, idempotency_key: str) -> dict[str, Any]:
        data = {"namespace": namespace, "workload": workload, "kind": kind}

        def call() -> tuple[str, Any, Any]:
            self._require_key(idempotency_key)
            target = require_workload(self.settings, namespace, workload)
            if target.kind != kind:
                raise _ActionFailure(ActuatorErrorCode.KIND_MISMATCH, "requested kind does not match registered target")
            writer = self._k8s_factory()
            marker = writer.read_marker(namespace, workload, kind)
            if marker == idempotency_key:
                snapshot = writer.snapshot(namespace, workload, kind)
                return "skipped_idempotent", snapshot, snapshot
            outcome = writer.restart(namespace, workload, kind, idempotency_key)
            return "applied", outcome["before"], outcome["after"]

        return await self._execute("restart_workload", call, data)

    async def scale_workload(self, namespace: str, workload: str, kind: str, replicas: int, idempotency_key: str) -> dict[str, Any]:
        data = {"namespace": namespace, "workload": workload, "kind": kind, "replicas": replicas}

        def call() -> tuple[str, Any, Any]:
            self._require_key(idempotency_key)
            target = require_workload(self.settings, namespace, workload)
            if target.kind != kind:
                raise _ActionFailure(ActuatorErrorCode.KIND_MISMATCH, "requested kind does not match registered target")
            require_replicas_in_bounds(target, replicas)
            writer = self._k8s_factory()
            marker = writer.read_marker(namespace, workload, kind)
            if marker == idempotency_key:
                snapshot = writer.snapshot(namespace, workload, kind)
                return "skipped_idempotent", snapshot, snapshot
            outcome = writer.scale(namespace, workload, kind, replicas, idempotency_key)
            return "applied", outcome["before"], outcome["after"]

        return await self._execute("scale_workload", call, data)

    async def rollback_workload(self, namespace: str, workload: str, kind: str, idempotency_key: str) -> dict[str, Any]:
        data = {"namespace": namespace, "workload": workload, "kind": kind}

        def call() -> tuple[str, Any, Any]:
            self._require_key(idempotency_key)
            target = require_workload(self.settings, namespace, workload)
            if target.kind != kind or kind != "deployment":
                raise _ActionFailure(ActuatorErrorCode.KIND_MISMATCH, "rollback only supports deployment workloads")
            writer = self._k8s_factory()
            marker = writer.read_marker(namespace, workload, kind)
            if marker == idempotency_key:
                snapshot = writer.snapshot(namespace, workload, kind)
                return "skipped_idempotent", snapshot, snapshot
            outcome = writer.rollback(namespace, workload, idempotency_key)
            return "applied", outcome["before"], outcome["after"]

        return await self._execute("rollback_workload", call, data)
