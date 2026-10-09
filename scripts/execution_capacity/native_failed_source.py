"""Bounded read-only v3 failed source replay; no copy or native_raw admission."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.acceptance.capacity_io import strict_json
from scripts.acceptance.capacity_models import NativeCommand
from scripts.execution_capacity.attempt import ReadOnlyAttemptLedger
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_failed_close import (
    NativeFailedHostCommitment,
    _receipt,
    _verify_failed_close_on_ledger,
)
from scripts.execution_capacity.native_failed_preflight import (
    COMMAND_BYTES,
    DRAIN_BYTES,
    FAILURE_BYTES,
    JOURNAL_BYTES,
    JOURNAL_ROWS,
    MANIFEST_BYTES,
    PLAN_BYTES,
    SHARD_BYTES,
    SHARDS,
    SOURCE_BYTES,
    WORK_BYTES,
    FixtureDiagnosticBudget,
    RunnerFailedOriginMap,
    preflight_failed_diagnostic,
)
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.reference_round import verify_round_records


@dataclass(frozen=True)
class FailedDiagnosticSourceReceipt:
    """Original-source diagnosis, never copy or successful source authority."""

    state: Literal["diagnostic-source-verified"]
    outcome: Literal["failed"]
    evidence_state: Literal["complete-evidence", "partial-evidence"]
    origins: RunnerFailedOriginMap
    command_sha256: str
    parent_plan_sha256: str
    parent_ledger_sha256: str
    child_plan_sha256: str
    child_ledger_sha256: str
    close_row_digest: str
    manifest_sha256: str
    failure_sha256: str


def _bounded_evidence_budget(budget: EvidenceBudget):
    if (
        type(budget) is not EvidenceBudget
        or budget.bytes_limit > WORK_BYTES
        or budget.work_bytes_limit > WORK_BYTES
        or budget.rows_limit > 4 * JOURNAL_ROWS + 100
        or budget.row_limit > JOURNAL_BYTES
    ):
        raise ValueError("shared finite failed diagnostic evidence budget required")


def verify_failed_diagnostic_source(
    trusted_origins: RunnerFailedOriginMap,
    command_sha256: str,
    *,
    fresh_host: NativeFailedHostCommitment,
    target_parent: Path,
    fixture_budget: FixtureDiagnosticBudget,
    evidence_budget: EvidenceBudget,
) -> FailedDiagnosticSourceReceipt:
    """Replay one failed original source after finite preflight and round binding.

    The runner supplies the original location map. This function does not infer
    its trust from copied paths, nor publish/copy/admit the verified source.
    """
    _bounded_evidence_budget(evidence_budget)
    preflight_failed_diagnostic(
        trusted_origins,
        command_sha256,
        fresh_host=fresh_host,
        target_parent=target_parent,
        budget=fixture_budget,
    )
    return _verify_failed_source_after_preflight(
        trusted_origins, command_sha256, fresh_host=fresh_host, evidence_budget=evidence_budget
    )


def _verify_failed_source_after_preflight(
    trusted_origins: RunnerFailedOriginMap,
    command_sha256: str,
    *,
    fresh_host: NativeFailedHostCommitment,
    evidence_budget: EvidenceBudget,
) -> FailedDiagnosticSourceReceipt:
    """Private replay after one caller-owned finite preflight; never trust a receipt."""
    parent = ReadOnlyAttemptLedger.open(
        trusted_origins.parent_retained,
        origin=trusted_origins.parent_origin,
        budget=evidence_budget,
        max_plan_bytes=PLAN_BYTES,
        max_journal_bytes=JOURNAL_BYTES,
        max_journal_rows=JOURNAL_ROWS,
    )
    child = ReadOnlyAttemptLedger.open(
        trusted_origins.child_retained,
        origin=trusted_origins.child_origin,
        budget=evidence_budget,
        max_plan_bytes=PLAN_BYTES,
        max_journal_bytes=JOURNAL_BYTES,
        max_journal_rows=JOURNAL_ROWS,
    )
    binding = verify_round_records(parent, child)
    if child.plan_sha256 != fresh_host.plan_sha256:
        raise ValueError("failed diagnostic child plan/host receipt differs")
    replayed = _verify_failed_close_on_ledger(
        child,
        command_sha256,
        physical_location=trusted_origins.child_retained,
        fixture_limits={
            "command": COMMAND_BYTES,
            "failure": FAILURE_BYTES,
            "drain": DRAIN_BYTES,
            "shard": SHARD_BYTES,
            "shards": SHARDS,
            "manifest": MANIFEST_BYTES,
            "native_total": SOURCE_BYTES,
        },
    )
    if replayed != fresh_host:
        raise ValueError("failed diagnostic fresh host close receipt differs")
    native_root = trusted_origins.child_retained / f"native-{command_sha256}"
    command_file, command_raw = _receipt(
        native_root, "command.json", COMMAND_BYTES, return_raw=True
    )
    if command_file.sha256 != command_sha256 or _canonical(strict_json(command_raw)) != command_raw:
        raise ValueError("failed diagnostic native command bytes differ")
    command = NativeCommand.model_validate(strict_json(command_raw))
    if (
        command.attempt_id != child.plan.get("attempt_id")
        or command.protocol_id != child.plan.get("protocol_id")
        or command.sample_id != binding.sample_id
        or command.window_id != binding.window_id
        or child.plan.get("protocol_id") != parent.plan.get("protocol_id")
    ):
        raise ValueError("failed diagnostic native command/round identity differs")
    return FailedDiagnosticSourceReceipt(
        state="diagnostic-source-verified",
        outcome="failed",
        evidence_state=replayed.evidence_state,
        origins=trusted_origins,
        command_sha256=command_sha256,
        parent_plan_sha256=parent.plan_sha256,
        parent_ledger_sha256=parent.ledger_sha256,
        child_plan_sha256=child.plan_sha256,
        child_ledger_sha256=child.ledger_sha256,
        close_row_digest=replayed.close_row_digest,
        manifest_sha256=replayed.manifest_sha256,
        failure_sha256=replayed.failure_sha256,
    )
