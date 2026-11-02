"""ConfigMap operation journal and per-target advancement gate.

All objects are namespaced. Immutable operation bindings are create-if-absent;
updates use the API server's resourceVersion compare-and-swap. There is no TTL
or delete path: terminal receipts double as permanent key tombstones. This is
deliberately stronger than the minimum 90 day retention promise.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from .config import ActuatorSettings
from .contracts import ActuatorErrorCode
from .k8s_writer import KUBERNETES_TIMEOUT, load_incluster_config
from .redaction import redact_text

MAX_JOURNAL_BYTES = 512 * 1024
RECEIPT_RESERVE_BYTES = 16 * 1024
TERMINAL_STATES = frozenset({"applied", "not_applied"})


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class JournalFailure(Exception):
    def __init__(self, code: ActuatorErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class JournalConflict(Exception):
    """The caller must reload; never overwrite a competing receipt or holder."""


@dataclass(frozen=True)
class JournalEntry:
    name: str
    version: str
    value: dict[str, Any]


def require_safe_patch(value: Any) -> None:
    """Reject secret-shaped data; never redact a patch and thereby change its meaning.

    Rollback templates with literal environment values or arbitrary commands are
    not safe to persist in a ConfigMap. Secret references are allowed: their values
    are never read. This intentionally conservative policy is in the capability.
    """
    if isinstance(value, str) and redact_text(value) != value:
        raise JournalFailure(ActuatorErrorCode.OPERATION_JOURNAL_SENSITIVE, "unsafe patch literal")
    if isinstance(value, list):
        for item in value:
            require_safe_patch(item)
    if isinstance(value, dict):
        for key, item in value.items():
            if item and key.lower() in {
                "value",
                "command",
                "args",
                "password",
                "passwd",
                "token",
                "secret",
                "authorization",
                "cookie",
                "dsn",
                "api_key",
                "apikey",
            }:
                raise JournalFailure(
                    ActuatorErrorCode.OPERATION_JOURNAL_SENSITIVE, "unsafe patch literal"
                )
            require_safe_patch(item)


class ConfigMapJournal:
    def __init__(self, settings: ActuatorSettings, *, core: Any = None) -> None:
        self.settings = settings
        if core is None:
            from kubernetes import client

            load_incluster_config()
            core = client.CoreV1Api()
        self.core = core

    def _name(self, kind: str, identity: Any) -> str:
        scope = [
            self.settings.provider_namespace,
            self.settings.target_ref,
            self.settings.caller_authority,
            identity,
        ]
        return f"oca-{kind}-{digest(scope)[:52]}"

    def key_name(self, key: str) -> str:
        return self._name("op", key)

    def operation_name(self, operation_id: str) -> str:
        return self._name("id", operation_id)

    def gate_name(self, target: dict[str, str]) -> str:
        # Target serialization covers all callers in this provider; including
        # authority here would permit two principals to overwrite recovery proof.
        identity = [
            self.settings.provider_namespace,
            self.settings.target_ref,
            target["namespace"],
            target["kind"],
            target["workload"],
        ]
        return f"oca-gate-{digest(identity)[:52]}"

    def read(self, name: str) -> JournalEntry | None:
        try:
            item = self.core.read_namespaced_config_map(
                name, self.settings.journal_namespace, _request_timeout=KUBERNETES_TIMEOUT
            )
            raw = item.data["record.json"]
            if len(raw.encode()) > MAX_JOURNAL_BYTES:
                raise ValueError("oversized journal")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise TypeError("invalid journal")
            return JournalEntry(name, str(item.metadata.resource_version), value)
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal read unavailable"
            ) from exc
        except (OSError, TimeoutError, ValueError, KeyError, TypeError, HTTPError) as exc:
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal read unavailable"
            ) from exc

    def _body(self, name: str, value: dict[str, Any], version: str | None) -> dict[str, Any]:
        body = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": self.settings.journal_namespace,
                "labels": {"app.kubernetes.io/managed-by": "opencitadel-ops-actuator"},
            },
            "data": {"record.json": canonical(value)},
        }
        if version:
            body["metadata"]["resourceVersion"] = version
        reserve = RECEIPT_RESERVE_BYTES if value.get("state") == "prepared" else 0
        if len(canonical(body).encode()) + reserve > MAX_JOURNAL_BYTES:
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_TOO_LARGE, "journal exceeds 512 KiB"
            )
        return body

    def create(self, name: str, value: dict[str, Any]) -> JournalEntry:
        body = self._body(name, value, None)
        try:
            item = self.core.create_namespaced_config_map(
                self.settings.journal_namespace, body, _request_timeout=KUBERNETES_TIMEOUT
            )
            return JournalEntry(name, str(item.metadata.resource_version), value)
        except ApiException as exc:
            if exc.status == 409:
                raise JournalConflict from exc
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal create unavailable"
            ) from exc
        except (OSError, TimeoutError, HTTPError) as exc:
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal create unavailable"
            ) from exc

    def replace(self, entry: JournalEntry, value: dict[str, Any]) -> JournalEntry:
        body = self._body(entry.name, value, entry.version)
        try:
            item = self.core.replace_namespaced_config_map(
                entry.name,
                self.settings.journal_namespace,
                body,
                _request_timeout=KUBERNETES_TIMEOUT,
            )
            return JournalEntry(entry.name, str(item.metadata.resource_version), value)
        except ApiException as exc:
            if exc.status == 409:
                raise JournalConflict from exc
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal update unavailable"
            ) from exc
        except (OSError, TimeoutError, HTTPError) as exc:
            raise JournalFailure(
                ActuatorErrorCode.OPERATION_JOURNAL_UNAVAILABLE, "journal update unavailable"
            ) from exc

    def bind_operation(self, operation_id: str, journal_name: str) -> None:
        name = self.operation_name(operation_id)
        value = {
            "schema_version": 1,
            "operation_id": operation_id,
            "journal_name": journal_name,
            "caller_authority": self.settings.caller_authority,
        }
        try:
            self.create(name, value)
        except JournalConflict:
            current = self.read(name)
            if current is None or current.value != value:
                raise JournalFailure(
                    ActuatorErrorCode.OPERATION_CONFLICT, "operation identity already bound"
                ) from None

    def find_operation(self, operation_id: str) -> JournalEntry | None:
        index = self.read(self.operation_name(operation_id))
        if index is None:
            return None
        if index.value.get("caller_authority") != self.settings.caller_authority:
            raise JournalFailure(ActuatorErrorCode.OPERATION_NOT_FOUND, "operation not found")
        return self.read(index.value["journal_name"])

    def acquire_target(self, operation: JournalEntry) -> bool:
        name = self.gate_name(operation.value["target"])
        value = {"schema_version": 1, "journal_name": operation.name}
        for _ in range(3):
            current = self.read(name)
            if current is None:
                try:
                    self.create(name, value)
                    return True
                except JournalConflict:
                    continue
            if current.value == value:
                return True
            previous = self.read(current.value["journal_name"])
            if previous is None or previous.value.get("state") not in TERMINAL_STATES:
                return False
            # The terminal receipt must already be durable before advancement.
            if not previous.value.get("receipt"):
                return False
            try:
                self.replace(current, value)
                return True
            except JournalConflict:
                continue
        return False
