"""Post-timing PostgreSQL diagnostic request and safe command-response binding.

C3 must record actual transport dispatch/receipt in its existing verified ledger.
These records are observations, not operator-provided aggregate success receipts.
C2c3 must acquire while services and original authorized connections remain open,
and continue safe cleanup if acquisition fails. No helper starts a service.
"""

from copy import deepcopy
from dataclasses import dataclass, field

from scripts.acceptance.capacity_io import canonical_digest


def one(ledger, kind, command_id):
    rows = [r["body"] for r in ledger.records(kind) if r["body"].get("command_id") == command_id]
    if len(rows) != 1:
        raise ValueError("missing/ambiguous actual diagnostic command record")
    return rows[0]


@dataclass(frozen=True)
class DiagnosticRequest:
    command_id: str
    identity: dict
    clock_id: str
    dispatched_ns: int
    sample_end_ns: int
    operation: str
    request_digest: str


def authorize_request(ledger, plan, sample, origin, *, command_id):
    row = one(ledger, "pg-diagnostics-dispatch", command_id)
    identity = row["identity"]
    if (
        origin.kind != "round"
        or sample.sample_id != plan.sample_id
        or identity
        != {
            "sample_id": plan.sample_id,
            "action_id": plan.action_id,
            "window_id": plan.physical_window_id,
            "round_id": origin.round.round_id,
            "boot_id": origin.boot_id,
            "clone_id": origin.clone_id,
            "observer_clock_id": identity.get("observer_clock_id"),
        }
        or not identity.get("observer_clock_id")
        or row.get("clock_id") != sample.clock_id
        or row.get("sample_end_ns") != sample.end_ns
        or row.get("operation") != plan.operation
        or type(row.get("host_ns")) is not int
        or row["host_ns"] < sample.end_ns
    ):
        raise ValueError("diagnostic request prewarms target or has wrong sample/clone/clock")
    return DiagnosticRequest(
        command_id,
        deepcopy(identity),
        sample.clock_id,
        row["host_ns"],
        sample.end_ns,
        plan.operation,
        canonical_digest(row),
    )


@dataclass(repr=False)
class DiagnosticResult:
    request: DiagnosticRequest
    database_digest: str
    build_digest: str
    started_ns: int
    ended_ns: int = 0
    statements: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    private: dict = field(default_factory=dict)
    capture_digest: str = "0" * 64
    buffer_origin: str = "post-timing-diagnostic-execution"

    def payload(self, *, budget=None):
        """Safe guest command body; raw expressions and credentials never serialize."""
        if self.buffer_origin == "original-timed-execution" and budget is None:
            raise ValueError("original diagnostic payload requires shared budget")
        if budget is None:
            private_digest = canonical_digest(self.private)
        else:
            from scripts.execution_capacity.evidence_json import json_digest

            # Compatibility commitment only; typed originals supply replay authority.
            private_digest = json_digest(self.private, budget=budget)
        return {
            "command_id": self.request.command_id,
            "identity": self.request.identity,
            "request_digest": self.request.request_digest,
            "database_digest": self.database_digest,
            "build_digest": self.build_digest,
            "started_ns": self.started_ns,
            "ended_ns": self.ended_ns,
            "statements": [s.model_dump(mode="json") for s in self.statements],
            "errors": list(self.errors),
            "private_digest": private_digest,
            "capture_digest": self.capture_digest,
            "buffer_origin": self.buffer_origin,
        }

    def export(self, ledger, *, budget=None):
        from scripts.acceptance.capacity_diagnostics import Query

        dispatch = one(ledger, "pg-diagnostics-dispatch", self.request.command_id)
        receipt = one(ledger, "pg-diagnostics-result", self.request.command_id)
        payload = self.payload(budget=budget)
        if (
            canonical_digest(dispatch) != self.request.request_digest
            or receipt.get("identity") != self.request.identity
            or receipt.get("clock_id") != self.request.clock_id
            or receipt.get("payload_digest") != canonical_digest(payload)
            or receipt.get("request_digest") != self.request.request_digest
            or type(receipt.get("host_ns")) is not int
            or receipt["host_ns"] < self.request.dispatched_ns
        ):
            raise ValueError("actual diagnostic transport response binding differs")
        identity = self.request.identity
        return Query(
            sample_id=identity["sample_id"],
            action_id=identity["action_id"],
            operation=self.request.operation,
            physical_window_id=identity["window_id"],
            clock_id=self.request.clock_id,
            collected_ns=self.request.dispatched_ns,
            ended_ns=receipt["host_ns"],
            clone_id=identity["clone_id"],
            timed_clone_id=identity["clone_id"],
            timed_end_ns=self.request.sample_end_ns,
            database_digest=self.database_digest,
            build_digest=self.build_digest,
            collection="pg_stat_statements+explain-analyze-buffers+pg_locks",
            buffer_origin=self.buffer_origin,
            statements=self.statements,
            errors=self.errors,
            observer_clock_id=identity["observer_clock_id"],
            observer_started_ns=self.started_ns,
            observer_ended_ns=self.ended_ns,
            boot_id=identity["boot_id"],
            round_id=identity["round_id"],
            command_id=self.request.command_id,
            request_digest=self.request.request_digest,
            response_digest=canonical_digest(payload),
            private_digest=payload["private_digest"],
            capture_digest=self.capture_digest,
        )

    def write_private(self, root, relative):
        from scripts.acceptance.capacity_io import write_relative
        from scripts.execution_capacity.attempt import encode

        write_relative(root, relative, encode(self.private))
