"""One-shot private stage copy of small failed diagnostics; no publication."""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.acceptance.capacity_io import strict_json
from scripts.execution_capacity.evidence_bounds import EvidenceBudget
from scripts.execution_capacity.native_failed_close import (
    NativeEvidenceManifestV3,
    NativeFailedHostCommitment,
    _exact_fileset,
)
from scripts.execution_capacity.native_failed_preflight import (
    COMMAND_BYTES,
    DRAIN_BYTES,
    FAILURE_BYTES,
    JOURNAL_BYTES,
    MANIFEST_BYTES,
    PLAN_BYTES,
    SHARD_BYTES,
    SHARDS,
    SOURCE_BYTES,
    FixtureDiagnosticBudget,
    RunnerFailedOriginMap,
    _journal_rows,
    _read_small,
    _stat_file,
    preflight_failed_diagnostic,
)
from scripts.execution_capacity.native_failed_source import (
    FailedDiagnosticSourceReceipt,
    _bounded_evidence_budget,
    _verify_failed_source_after_preflight,
)
from scripts.execution_capacity.native_raw import _canonical
from scripts.execution_capacity.proof_copy import (
    _identity,
    _regular,
    _stream,
    copy_private,
    directory,
    parent,
)


@dataclass(frozen=True)
class FailedDiagnosticCopiedReceipt:
    """Verified private stage only, not published or native_raw authority."""

    state: Literal["diagnostic-copied-verified"]
    outcome: Literal["failed"]
    evidence_state: Literal["complete-evidence", "partial-evidence"]
    stage: Path
    origins: RunnerFailedOriginMap
    command_sha256: str
    parent_plan_sha256: str
    parent_ledger_sha256: str
    child_plan_sha256: str
    child_ledger_sha256: str
    close_row_digest: str
    manifest_sha256: str
    failure_sha256: str


def _source_inventory(origins, key, host, sizes):
    native = origins.child_retained / f"native-{key}"
    with directory(native) as native_fd:
        raw = _read_small(native_fd, "manifest.json", MANIFEST_BYTES)
    if (
        hashlib.sha256(raw).hexdigest() != host.manifest_sha256
        or _canonical(strict_json(raw)) != raw
    ):
        raise ValueError("failed diagnostic source manifest changed before copy")
    manifest = NativeEvidenceManifestV3.model_validate(strict_json(raw))
    members = {
        "parent": {
            "plan.json": {
                "sha256": sizes["parent_plan_sha"],
                "size_bytes": sizes["parent/plan.json"],
            },
            "attempt.jsonl": {
                "sha256": sizes["parent_journal_sha"],
                "size_bytes": sizes["parent/attempt.jsonl"],
            },
        },
        "child": {
            "plan.json": {
                "sha256": sizes["child_plan_sha"],
                "size_bytes": sizes["child/plan.json"],
            },
            "attempt.jsonl": {
                "sha256": sizes["child_journal_sha"],
                "size_bytes": sizes["child/attempt.jsonl"],
            },
            f"native-{key}/manifest.json": {"sha256": host.manifest_sha256, "size_bytes": len(raw)},
        },
    }
    for descriptor in (
        manifest.command,
        manifest.failure,
        manifest.failure_drain,
        *manifest.shards,
    ):
        relative = f"native-{key}/{descriptor.path}"
        if sizes[f"child/{relative}"] != descriptor.size_bytes:
            raise ValueError("failed diagnostic source inventory size changed")
        members["child"][relative] = {
            "sha256": descriptor.sha256,
            "size_bytes": descriptor.size_bytes,
        }
    if sizes[f"child/native-{key}/manifest.json"] != len(raw):
        raise ValueError("failed diagnostic source manifest size changed")
    return members


def _stage_probe(stage: Path, key: str, host: NativeFailedHostCommitment, members):
    """Private bounded stage-only metadata probe; no ancestry or budget charge."""
    native_name = f"native-{key}"
    with directory(stage) as stage_fd:
        if set(os.listdir(stage_fd)) != {"parent", "child"}:
            raise ValueError("failed diagnostic stage root fileset differs")
    with (
        directory(stage / "parent") as parent_fd,
        directory(stage / "child") as child_fd,
        directory(stage / "child" / native_name) as native_fd,
        directory(stage / "child" / native_name / "records") as records_fd,
    ):
        if (
            set(os.listdir(parent_fd)) != {"plan.json", "attempt.jsonl"}
            or set(os.listdir(child_fd)) != {"plan.json", "attempt.jsonl", native_name}
            or set(os.listdir(native_fd))
            != {"manifest.json", "command.json", "failure.json", "failure-drain.ndjson", "records"}
        ):
            raise ValueError("failed diagnostic stage exact fileset differs")
        manifest_raw = _read_small(native_fd, "manifest.json", MANIFEST_BYTES)
        if (
            hashlib.sha256(manifest_raw).hexdigest() != host.manifest_sha256
            or _canonical(strict_json(manifest_raw)) != manifest_raw
        ):
            raise ValueError("failed diagnostic stage manifest differs")
        manifest = NativeEvidenceManifestV3.model_validate(strict_json(manifest_raw))
        if len(manifest.shards) > SHARDS or set(os.listdir(records_fd)) != {
            row.path.split("/", 1)[1] for row in manifest.shards
        }:
            raise ValueError("failed diagnostic stage record fileset differs")
        limits = {
            "plan.json": PLAN_BYTES,
            "attempt.jsonl": JOURNAL_BYTES,
            "manifest.json": MANIFEST_BYTES,
            "command.json": COMMAND_BYTES,
            "failure.json": FAILURE_BYTES,
            "failure-drain.ndjson": DRAIN_BYTES,
        }
        total = 0
        for label, root_fd in (("parent", parent_fd), ("child", child_fd)):
            for name in ("plan.json", "attempt.jsonl"):
                size = _stat_file(root_fd, name, limits[name], nonempty=name == "plan.json")
                if size != members[label][name]["size_bytes"]:
                    raise ValueError("failed diagnostic stage companion size differs")
                total += size
            _journal_rows(root_fd, members[label]["attempt.jsonl"]["size_bytes"])
        for name in ("manifest.json", "command.json", "failure.json", "failure-drain.ndjson"):
            size = _stat_file(
                native_fd, name, limits[name], nonempty=name != "failure-drain.ndjson"
            )
            if size != members["child"][f"{native_name}/{name}"]["size_bytes"]:
                raise ValueError("failed diagnostic stage native size differs")
            total += size
        for row in manifest.shards:
            name = row.path.split("/", 1)[1]
            size = _stat_file(records_fd, name, SHARD_BYTES, nonempty=True)
            if size != members["child"][f"{native_name}/{row.path}"]["size_bytes"]:
                raise ValueError("failed diagnostic stage shard size differs")
            total += size
        if total > SOURCE_BYTES:
            raise ValueError("failed diagnostic stage total exceeds fixture bound")
        return total


def _hash_source_after_copy(root: Path, members: dict, *, budget: EvidenceBudget):
    with directory(root) as root_fd:
        for relative, expected in members.items():
            with parent(root_fd, relative) as (parent_fd, name):
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                try:
                    before = _regular(fd)
                    _stream(fd, expected, budget=budget)
                    if _identity(before) != _identity(
                        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    ):
                        raise ValueError("failed diagnostic source path changed after copy")
                finally:
                    os.close(fd)


def copy_failed_diagnostic_stage(
    trusted_origins: RunnerFailedOriginMap,
    command_sha256: str,
    *,
    fresh_host: NativeFailedHostCommitment,
    target_parent: Path,
    stage_name: str,
    fixture_budget: FixtureDiagnosticBudget,
    evidence_budget: EvidenceBudget,
    claimed_source: FailedDiagnosticSourceReceipt | None = None,
) -> FailedDiagnosticCopiedReceipt:
    """One preflight, one O_EXCL stage, independent replay; never publish."""
    _bounded_evidence_budget(evidence_budget)
    if type(stage_name) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", stage_name) is None:
        raise ValueError("unique private failed diagnostic stage name required")
    preflight = preflight_failed_diagnostic(
        trusted_origins,
        command_sha256,
        fresh_host=fresh_host,
        target_parent=target_parent,
        budget=fixture_budget,
    )
    operation_budget = evidence_budget.child(
        bytes_limit=preflight.work_reserved_bytes,
        work_bytes_limit=preflight.work_reserved_bytes,
    )
    operation_budget.reserve(preflight.source_bytes * 3, rows=0)
    source = _verify_failed_source_after_preflight(
        trusted_origins,
        command_sha256,
        fresh_host=fresh_host,
        evidence_budget=operation_budget,
    )
    if claimed_source is not None and (
        type(claimed_source) is not FailedDiagnosticSourceReceipt or claimed_source != source
    ):
        raise ValueError("claimed failed diagnostic source receipt stale or forged")
    sizes = dict(preflight.members)
    sizes.update(
        parent_plan_sha=source.parent_plan_sha256,
        parent_journal_sha=source.parent_ledger_sha256,
        child_plan_sha=source.child_plan_sha256,
        child_journal_sha=source.child_ledger_sha256,
    )
    inventory = _source_inventory(trusted_origins, command_sha256, fresh_host, sizes)
    stage = target_parent / stage_name
    with directory(target_parent) as target_fd:
        fixture_budget.recheck_target_space(target_fd)
        os.mkdir(stage_name, mode=0o700, dir_fd=target_fd)
        os.fsync(target_fd)
    with directory(stage) as stage_fd:
        os.fsync(stage_fd)
    copy_private(
        trusted_origins.parent_retained,
        stage / "parent",
        inventory["parent"],
        budget=operation_budget,
    )
    copy_private(
        trusted_origins.child_retained, stage / "child", inventory["child"], budget=operation_budget
    )
    if not any(
        relative.startswith(f"native-{command_sha256}/records/") for relative in inventory["child"]
    ):
        with directory(stage / "child" / f"native-{command_sha256}") as native_fd:
            os.mkdir("records", mode=0o700, dir_fd=native_fd)
            os.fsync(native_fd)
    operation_budget.reserve(preflight.source_bytes, rows=0)
    if _stage_probe(stage, command_sha256, fresh_host, inventory) != preflight.source_bytes:
        raise ValueError("failed diagnostic stage total differs from source preflight")
    stage_origins = RunnerFailedOriginMap(
        trusted_origins.parent_origin,
        stage / "parent",
        trusted_origins.child_origin,
        stage / "child",
    )
    operation_budget.reserve(preflight.source_bytes * 3, rows=0)
    replayed = _verify_failed_source_after_preflight(
        stage_origins,
        command_sha256,
        fresh_host=fresh_host,
        evidence_budget=operation_budget,
    )
    if (
        source.command_sha256 != replayed.command_sha256
        or source.parent_plan_sha256 != replayed.parent_plan_sha256
        or source.parent_ledger_sha256 != replayed.parent_ledger_sha256
        or source.child_plan_sha256 != replayed.child_plan_sha256
        or source.child_ledger_sha256 != replayed.child_ledger_sha256
        or source.close_row_digest != replayed.close_row_digest
        or source.manifest_sha256 != replayed.manifest_sha256
        or source.failure_sha256 != replayed.failure_sha256
        or source.evidence_state != replayed.evidence_state
    ):
        raise ValueError("failed diagnostic independent stage replay differs")
    _hash_source_after_copy(
        trusted_origins.parent_retained, inventory["parent"], budget=operation_budget
    )
    _hash_source_after_copy(
        trusted_origins.child_retained, inventory["child"], budget=operation_budget
    )
    _hash_source_after_copy(stage / "parent", inventory["parent"], budget=operation_budget)
    _hash_source_after_copy(stage / "child", inventory["child"], budget=operation_budget)
    _exact_fileset(
        trusted_origins.child_retained / f"native-{command_sha256}",
        {
            relative.removeprefix(f"native-{command_sha256}/")
            for relative in inventory["child"]
            if relative.startswith(f"native-{command_sha256}/records/")
        },
        with_manifest=True,
    )
    if _stage_probe(stage, command_sha256, fresh_host, inventory) != preflight.source_bytes:
        raise ValueError("failed diagnostic stage changed after independent replay")
    return FailedDiagnosticCopiedReceipt(
        state="diagnostic-copied-verified",
        outcome="failed",
        evidence_state=replayed.evidence_state,
        stage=stage,
        origins=trusted_origins,
        command_sha256=command_sha256,
        parent_plan_sha256=replayed.parent_plan_sha256,
        parent_ledger_sha256=replayed.parent_ledger_sha256,
        child_plan_sha256=replayed.child_plan_sha256,
        child_ledger_sha256=replayed.child_ledger_sha256,
        close_row_digest=replayed.close_row_digest,
        manifest_sha256=replayed.manifest_sha256,
        failure_sha256=replayed.failure_sha256,
    )
