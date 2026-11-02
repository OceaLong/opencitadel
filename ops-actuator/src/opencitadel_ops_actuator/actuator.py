"""Registered writes with durable identities, fixed patches and conservative recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes.client.exceptions import ApiException
from pydantic import ValidationError
from urllib3.exceptions import HTTPError

from .config import ActuatorSettings
from .contracts import (
    ActuatorEnvelope,
    ActuatorErrorCode,
    EvidenceItem,
    GetOperationRequest,
    GetTargetRequest,
    RestartRequest,
    RollbackRequest,
    ScaleRequest,
    TargetIdentity,
    operation_payload_digest,
)
from .k8s_writer import (
    OPERATION_ANNOTATION,
    PAYLOAD_ANNOTATION,
    KubernetesWriter,
    NoRollbackRevision,
    WorkloadNotFound,
)
from .operation_journal import (
    TERMINAL_STATES,
    ConfigMapJournal,
    JournalConflict,
    JournalEntry,
    JournalFailure,
    canonical,
    digest,
    require_safe_patch,
)
from .security import TargetDenied, require_replicas_in_bounds, require_workload


class Actuator:
    def __init__(
        self,
        settings: ActuatorSettings,
        *,
        k8s_factory: Callable[[], Any] = KubernetesWriter,
        journal_factory: Callable[[], ConfigMapJournal] | None = None,
    ) -> None:
        self.settings = settings
        self._k8s_factory = k8s_factory
        self._journal_factory = journal_factory or (lambda: ConfigMapJournal(settings))
        self._semaphore = asyncio.Semaphore(settings.concurrency)

    def _response(
        self,
        record: dict[str, Any],
        *,
        replay: bool = False,
        error: ActuatorErrorCode | None = None,
    ) -> dict[str, Any]:
        state = record["state"]
        public_state = "pending" if state in {"preparing", "prepared", "sending"} else state
        receipt = record.get("receipt")
        outcome = {
            "applied": "skipped_idempotent" if replay else "applied",
            "not_applied": "failed",
            "unknown": "unknown",
            "pending": "pending",
        }[public_state]
        evidence = []
        if receipt:
            evidence = [
                EvidenceItem(
                    type="actuator_result",
                    ref=f"actuator://operations/{record['operation_id']}/receipts/{record['revision']}",
                    sha256=digest(receipt),
                    target_ref=self.settings.target_ref,
                    expires_at=datetime.now(UTC) + timedelta(days=90),
                )
            ]
        result = ActuatorEnvelope(
            target_ref=self.settings.target_ref,
            action=record["action"],
            action_outcome=outcome,
            operation_id=record["operation_id"],
            state=public_state,
            receipt_revision=record["revision"],
            payload_digest=record["payload_digest"],
            key_digest=record["key_digest"],
            target=record["target"],
            receipt=receipt,
            before=record.get("before"),
            after=receipt.get("after") if receipt else None,
            data=record.get("parameters", {}),
            evidence=evidence,
            error_code=error or (receipt.get("error_code") if receipt else None),
        ).model_dump(mode="json")
        if len(canonical(result).encode()) > self.settings.max_output_bytes:
            # Preserve the complete verifiable receipt before optional duplicate
            # snapshots. Never truncate an identity, digest or receipt silently.
            result.update(
                before=None, after=None, data={}, warnings=["duplicate_snapshots_omitted"]
            )
        if len(canonical(result).encode()) > self.settings.max_output_bytes:
            return self._failure(
                record["action"],
                record["operation_id"],
                ActuatorErrorCode.OUTPUT_TOO_LARGE,
                unknown=True,
            )
        return result

    def _failure(
        self,
        action: str,
        operation_id: str,
        code: ActuatorErrorCode,
        *,
        unknown: bool = False,
    ) -> dict[str, Any]:
        # No raw K8s exception text: it can contain credential-bearing request bodies.
        return ActuatorEnvelope(
            target_ref=self.settings.target_ref,
            action=action,
            action_outcome="unknown" if unknown else "failed",
            operation_id=operation_id,
            # This rejects the current request, not the historical operation.
            # Only a durable rejection receipt can assert not_applied: a retry
            # may arrive after an earlier successful send or permission change.
            state="unknown",
            error_code=code,
            error_message=code.value,
        ).model_dump(mode="json")

    def _binding(
        self,
        action: str,
        target: dict[str, str],
        key: str,
        operation_id: str,
        payload_digest: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "operation_id": operation_id,
            "action": action,
            "payload_digest": payload_digest,
            "key_digest": digest(key),
            "caller_authority": self.settings.caller_authority,
            "provider_namespace": self.settings.provider_namespace,
            "target": target,
            "parameters": parameters,
        }

    @staticmethod
    def _validate_binding(entry: JournalEntry, binding: dict[str, Any]) -> None:
        if any(entry.value.get(k) != v for k, v in binding.items()):
            raise JournalFailure(ActuatorErrorCode.OPERATION_CONFLICT, "operation binding mismatch")

    def _finish(
        self,
        journal: ConfigMapJournal,
        entry: JournalEntry,
        after: dict[str, Any] | None,
        *,
        rejection: ActuatorErrorCode | None = None,
    ) -> JournalEntry:
        for _ in range(3):
            if entry.value["state"] in TERMINAL_STATES:
                return entry
            if rejection and entry.value["state"] != "preparing":
                return entry
            revision = entry.value["revision"] + 1
            receipt = {
                "schema_version": 1,
                "operation_id": entry.value["operation_id"],
                "caller_authority": entry.value["caller_authority"],
                "provider_namespace": entry.value["provider_namespace"],
                "payload_digest": entry.value["payload_digest"],
                "key_digest": entry.value["key_digest"],
                "target": entry.value["target"],
                "action": entry.value["action"],
                "state": "not_applied" if rejection else "applied",
                "revision": revision,
                "before": entry.value.get("before"),
                "after": {**after, **entry.value.get("after_hint", {})} if after else None,
                "error_code": rejection.value if rejection else None,
                "recorded_at": datetime.now(UTC).isoformat(),
            }
            value = {
                **entry.value,
                "state": receipt["state"],
                "revision": revision,
                "receipt": receipt,
            }
            try:
                return journal.replace(entry, value)
            except JournalConflict:
                current = journal.read(entry.name)
                if current is None:
                    raise JournalFailure(
                        ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal disappeared"
                    ) from None
                entry = current
        raise JournalFailure(ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "receipt CAS busy")

    def _resolve(
        self,
        journal: ConfigMapJournal,
        writer: Any,
        entry: JournalEntry,
    ) -> JournalEntry:
        if entry.value["state"] in TERMINAL_STATES:
            return entry
        if entry.value["state"] == "preparing":
            return entry
        try:
            observed = writer.observe(entry.value["target"])
            markers = observed["annotations"]
            if (
                observed["snapshot"]["uid"] == entry.value["target"]["uid"]
                and markers.get(OPERATION_ANNOTATION) == entry.value["operation_id"]
                and markers.get(PAYLOAD_ANNOTATION) == entry.value["payload_digest"]
            ):
                return self._finish(journal, entry, observed["snapshot"])
        except (ApiException, WorkloadNotFound, OSError, TimeoutError, RuntimeError, HTTPError):
            pass
        # Absence/overwrite/recreation/read failure is NOT evidence of not_applied.
        if entry.value["state"] != "prepared":
            try:
                return journal.replace(entry, {**entry.value, "state": "unknown"})
            except JournalConflict:
                return journal.read(entry.name) or entry
        return entry

    def _run(
        self,
        action: str,
        namespace: str,
        workload: str,
        kind: str,
        key: str,
        operation_id: str,
        payload_digest: str,
        target_uid: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        entry: JournalEntry | None = None
        dispatched = False
        try:
            if not key or not key.strip():
                raise JournalFailure(ActuatorErrorCode.IDEMPOTENCY_KEY_MISSING, "key required")
            request_type = {
                "restart_workload": RestartRequest,
                "scale_workload": ScaleRequest,
                "rollback_workload": RollbackRequest,
            }[action]
            request_type(
                namespace=namespace,
                workload=workload,
                kind=kind,
                idempotency_key=key,
                operation_id=operation_id,
                payload_digest=payload_digest,
                target_uid=target_uid,
                **parameters,
            )
            registered = require_workload(self.settings, namespace, workload)
            if registered.kind != kind or (action == "rollback_workload" and kind != "deployment"):
                raise JournalFailure(ActuatorErrorCode.KIND_MISMATCH, "kind mismatch")
            if action == "scale_workload":
                require_replicas_in_bounds(registered, parameters["replicas"])
            if not operation_id or not target_uid:
                raise JournalFailure(ActuatorErrorCode.OPERATION_CONFLICT, "identity required")
            expected = operation_payload_digest(
                action, self.settings.target_ref, namespace, workload, kind, target_uid, parameters
            )
            if payload_digest != expected:
                raise JournalFailure(ActuatorErrorCode.PAYLOAD_DIGEST_MISMATCH, "digest mismatch")
            target = {
                "cluster": self.settings.target_ref,
                "namespace": namespace,
                "workload": workload,
                "kind": kind,
                "uid": target_uid,
            }
            binding = self._binding(action, target, key, operation_id, payload_digest, parameters)
            journal = self._journal_factory()
            name = journal.key_name(key)
            entry = journal.read(name)
            if entry is None:
                value = {
                    "schema_version": 1,
                    **binding,
                    "state": "preparing",
                    "revision": 0,
                    "created_at": datetime.now(UTC).isoformat(),
                    "receipt": None,
                }
                try:
                    entry = journal.create(name, value)
                except JournalConflict:
                    entry = journal.read(name)
            if entry is None:
                raise JournalFailure(
                    ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "missing journal"
                )
            self._validate_binding(entry, binding)
            journal.bind_operation(operation_id, name)
            if entry.value["state"] in TERMINAL_STATES:
                return self._response(entry.value, replay=True)
            if not journal.acquire_target(entry):
                return self._response(entry.value, error=ActuatorErrorCode.OPERATION_PENDING)
            writer = self._k8s_factory()
            if entry.value["state"] == "preparing":
                try:
                    prepared = writer.prepare(
                        action, target, operation_id, payload_digest, parameters
                    )
                    require_safe_patch(prepared["patch"])
                    entry = journal.replace(entry, {**entry.value, **prepared, "state": "prepared"})
                except JournalConflict:
                    current = journal.read(entry.name)
                    if current is None:
                        raise JournalFailure(
                            ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "missing journal"
                        ) from None
                    entry = current
                except (NoRollbackRevision, WorkloadNotFound, ValueError, JournalFailure) as exc:
                    if isinstance(exc, JournalFailure):
                        if exc.code == ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE:
                            raise
                        code = exc.code
                    elif isinstance(exc, NoRollbackRevision):
                        code = ActuatorErrorCode.NO_ROLLBACK_REVISION
                    elif isinstance(exc, WorkloadNotFound):
                        code = ActuatorErrorCode.TARGET_NOT_FOUND
                    else:
                        code = (
                            ActuatorErrorCode.TARGET_UID_MISMATCH
                            if str(exc) == "TARGET_UID_MISMATCH"
                            else ActuatorErrorCode.INTERNAL
                        )
                    return self._response(self._finish(journal, entry, None, rejection=code).value)
            if entry.value["state"] in TERMINAL_STATES:
                return self._response(entry.value, replay=True)
            if entry.value["state"] != "prepared":
                entry = self._resolve(journal, writer, entry)
                if entry.value["state"] in TERMINAL_STATES:
                    return self._response(entry.value, replay=True)
            # Claim permission durably before sending. A concurrent contender can
            # only submit the *identical* patch with the original resourceVersion.
            try:
                entry = journal.replace(entry, {**entry.value, "state": "sending"})
            except JournalConflict:
                current = journal.read(entry.name)
                if current is None:
                    raise JournalFailure(
                        ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "missing journal"
                    ) from None
                return self._response(self._resolve(journal, writer, current).value, replay=True)
            dispatched = True
            try:
                after = writer.apply_prepared(target, entry.value["patch"])
            except (ApiException, OSError, TimeoutError, RuntimeError, WorkloadNotFound, HTTPError):
                entry = self._resolve(journal, writer, entry)
                return self._response(
                    entry.value,
                    error=(
                        None
                        if entry.value["state"] == "applied"
                        else ActuatorErrorCode.OPERATION_UNKNOWN
                    ),
                )
            entry = self._finish(journal, entry, after)
            return self._response(entry.value)
        except TargetDenied as exc:
            return self._failure(action, operation_id, ActuatorErrorCode(str(exc)))
        except JournalFailure as exc:
            # Existing or sent operations never become definitive failures merely
            # because the journal is currently unavailable.
            return self._failure(
                action,
                operation_id,
                exc.code,
                unknown=dispatched
                or exc.code == ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE
                or (entry is not None and entry.value["state"] not in {"preparing", "prepared"}),
            )
        except NoRollbackRevision:
            return self._failure(action, operation_id, ActuatorErrorCode.NO_ROLLBACK_REVISION)
        except WorkloadNotFound:
            return self._failure(action, operation_id, ActuatorErrorCode.TARGET_NOT_FOUND)
        except ValidationError:
            return self._failure(action, operation_id[:200], ActuatorErrorCode.OPERATION_CONFLICT)
        except ValueError as exc:
            code = (
                ActuatorErrorCode.TARGET_UID_MISMATCH
                if str(exc) == "TARGET_UID_MISMATCH"
                else ActuatorErrorCode.INTERNAL
            )
            return self._failure(action, operation_id, code, unknown=dispatched)
        except (ApiException, OSError, RuntimeError, HTTPError):
            return self._failure(
                action, operation_id, ActuatorErrorCode.K8S_ERROR, unknown=dispatched
            )

    async def _offload(self, call: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        async with self._semaphore:
            # Synchronous Kubernetes calls must not block all HTTP/resolve tasks.
            task = asyncio.create_task(asyncio.to_thread(call, *args))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # Client disconnect does not cancel a running thread or retract a
                # Kubernetes write. Keep its slot until the bounded call finishes.
                try:
                    await asyncio.shield(task)
                finally:
                    raise

    async def _execute(self, *args: Any) -> dict[str, Any]:
        return await self._offload(self._run, *args)

    async def get_target(self, namespace: str, workload: str, kind: str) -> dict[str, Any]:
        """Resolve a registered target's current UID before platform approval."""

        def read() -> dict[str, Any]:
            request = GetTargetRequest(namespace=namespace, workload=workload, kind=kind)
            target = require_workload(self.settings, namespace, workload)
            if target.kind != request.kind:
                raise TargetDenied("KIND_MISMATCH")
            try:
                observed = self._k8s_factory().observe(
                    {"namespace": namespace, "workload": workload, "kind": kind}
                )
            except (ApiException, OSError, RuntimeError, HTTPError, WorkloadNotFound):
                raise RuntimeError("registered target identity unavailable") from None
            return TargetIdentity(
                target_ref=self.settings.target_ref,
                namespace=namespace,
                workload=workload,
                kind=kind,
                uid=observed["snapshot"]["uid"],
            ).model_dump(mode="json")

        return await self._offload(read)

    async def restart_workload(
        self,
        namespace: str,
        workload: str,
        kind: str,
        idempotency_key: str,
        operation_id: str,
        payload_digest: str,
        target_uid: str,
    ) -> dict[str, Any]:
        return await self._execute(
            "restart_workload",
            namespace,
            workload,
            kind,
            idempotency_key,
            operation_id,
            payload_digest,
            target_uid,
            {},
        )

    async def scale_workload(
        self,
        namespace: str,
        workload: str,
        kind: str,
        replicas: int,
        idempotency_key: str,
        operation_id: str,
        payload_digest: str,
        target_uid: str,
    ) -> dict[str, Any]:
        return await self._execute(
            "scale_workload",
            namespace,
            workload,
            kind,
            idempotency_key,
            operation_id,
            payload_digest,
            target_uid,
            {"replicas": replicas},
        )

    async def rollback_workload(
        self,
        namespace: str,
        workload: str,
        kind: str,
        idempotency_key: str,
        operation_id: str,
        payload_digest: str,
        target_uid: str,
    ) -> dict[str, Any]:
        return await self._execute(
            "rollback_workload",
            namespace,
            workload,
            kind,
            idempotency_key,
            operation_id,
            payload_digest,
            target_uid,
            {},
        )

    def _query(self, operation_id: str) -> dict[str, Any]:
        try:
            GetOperationRequest(operation_id=operation_id)
            journal = self._journal_factory()
            entry = journal.find_operation(operation_id)
            if entry is None or entry.value.get("operation_id") != operation_id:
                return self._failure(
                    "get_operation",
                    operation_id,
                    ActuatorErrorCode.OPERATION_NOT_FOUND,
                    unknown=True,
                )
            target = entry.value["target"]
            registered = require_workload(self.settings, target["namespace"], target["workload"])
            if registered.kind != target["kind"]:
                raise TargetDenied("TARGET_DENIED")
            if entry.value["caller_authority"] != self.settings.caller_authority:
                raise TargetDenied("TARGET_DENIED")
            result = (
                entry
                if entry.value["state"] in TERMINAL_STATES
                else self._resolve(journal, self._k8s_factory(), entry)
            )
            return self._response(result.value, replay=True)
        except TargetDenied:
            return self._failure(
                "get_operation", operation_id, ActuatorErrorCode.TARGET_DENIED, unknown=True
            )
        except ValidationError:
            return self._failure(
                "get_operation",
                operation_id[:200],
                ActuatorErrorCode.OPERATION_NOT_FOUND,
                unknown=True,
            )
        except (JournalFailure, ApiException, OSError, RuntimeError, HTTPError):
            return self._failure(
                "get_operation",
                operation_id,
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE,
                unknown=True,
            )

    async def get_operation(self, operation_id: str) -> dict[str, Any]:
        return await self._offload(self._query, operation_id)
