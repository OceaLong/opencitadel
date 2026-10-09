"""Current-owned two-phase private readback of expected Judge timeout retention.

No budget/dispatch/lease/archive mutation and no service lifecycle changes. The
normal public archive rejection is followed by a second fresh read-only snapshot.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import UUID

from scripts.acceptance.protected_retention import (
    COMPARISON_POLICY,
    SAFE_REASONS,
    TABLES,
    scheduler_clock_evidence,
    validate_snapshot,
    verify_retention,
)
from scripts.acceptance.protected_retention_probe import safe_error, source_digest
from scripts.acceptance.strict_bridge import assert_kernel_identity, atomic_json, read_json


def expectation(root, binding, batch_id):
    owned = []
    for path in (root / "cleanup-journal" / "pending").glob("*.json"):
        entry = read_json(path)
        action = entry.get("value", {})
        if (
            entry.get("run_id") == binding["run_id"]
            and action.get("action") == "delete-resource"
            and action.get("resource") == "evaluation-batch"
            and action.get("resource_id") == batch_id
            and action.get("expected_unknown_retention") is True
            and action.get("workspace_id") is None
        ):
            owned.append(entry)
    if len(owned) != 1:
        raise ValueError("exact current-owned expected timeout cleanup missing")
    records = read_json(root / "evaluation-lifecycle.json")
    matches = [row for row in records if row.get("batch", {}).get("id") == batch_id]
    if len(matches) != 1:
        raise ValueError("exact native timeout expectation missing")
    record = matches[0]
    errors = [
        row
        for row in record.get("history", {}).get("items", [])
        if row.get("score", {}).get("source") == "model"
        and row["score"].get("status") == "error"
        and row["score"].get("value") is None
        and row["score"].get("reason") == "judge_execution_failed"
        and row.get("judge_run_id")
    ]
    if (
        record.get("scenario") != "judge-timeout"
        or record.get("mode") not in {"recorded", "isolated"}
        or not errors
    ):
        raise ValueError("only actual original null Judge timeout history may be retained")
    return record, errors


def collect(root, batch_id, phase, execute=subprocess.run):
    UUID(batch_id)
    if phase not in {"before", "after"}:
        raise ValueError("invalid retention phase")
    root = Path(root)
    binding = read_json(root / "strict-binding.json")
    bootstrap = read_json(root / "strict-bootstrap.json")
    if (binding["run_id"], binding["project"], binding["invocation_id"]) != (
        os.environ["ACCEPTANCE_RUN_ID"],
        os.environ["ACCEPTANCE_PROJECT_ID"],
        os.environ["ACCEPTANCE_STRICT_INVOCATION_ID"],
    ):
        raise ValueError("foreign protected retention collector")
    if (bootstrap["run_id"], bootstrap["project"], bootstrap["scope"]) != (
        binding["run_id"],
        binding["project"],
        {"type": "personal", "user_id": bootstrap["operator_id"], "team_id": None},
    ):
        raise ValueError("foreign retention bootstrap owner")
    record, errors = expectation(root, binding, batch_id)
    prefix = f"protected-retention-{batch_id}"
    raw_path = root / f"{prefix}.{phase}.raw.json"
    receipt_path = root / (f"{prefix}.before.json" if phase == "before" else f"{prefix}.json")
    if raw_path.exists() or receipt_path.exists():
        raise ValueError("retention snapshot may not be overwritten")
    before = None
    before_sha = None
    if phase == "after":
        initial = read_json(root / f"{prefix}.before.json")
        before_path = root / f"{prefix}.before.raw.json"
        before = read_json(before_path)
        before_sha = hashlib.sha256(before_path.read_bytes()).hexdigest()
        expected = validate_snapshot(before, batch_id=batch_id, owner_id=bootstrap["operator_id"])
        if (
            initial
            != {
                "schema_version": 1,
                "binding": binding,
                "status": "protected-retention-observed",
                "raw_sha256": before_sha,
                "verification": expected,
            }
            or before.get("source_sha256") != source_digest()
        ):
            raise ValueError("stale or foreign before snapshot")
    container = binding["kernel_container"]

    def inspect():
        observed = execute(
            ["docker", "inspect", container], check=True, capture_output=True, timeout=30
        )
        assert_kernel_identity(json.loads(observed.stdout)[0], binding, running=True)

    inspect()
    diagnostic_ready = False
    try:
        observed = execute(
            [
                "docker",
                "exec",
                "-i",
                container,
                "/app/.venv/bin/python",
                "/acceptance-driver/protected_retention_probe.py",
            ],
            input=json.dumps(
                {"driver": {"binding": binding, "bootstrap": bootstrap}, "batch_id": batch_id}
            ).encode(),
            check=True,
            capture_output=True,
            timeout=45,
        )
    except subprocess.CalledProcessError as error:
        failure = {"stage": "probe-execution", "exception": type(error).__name__}
        try:
            failed_body = json.loads(error.stdout or b"{}")
            candidate = failed_body.get("error")
        except (ValueError, TypeError):
            candidate = None
            failed_body = {}
        if (
            isinstance(candidate, dict)
            and set(candidate) <= {"stage", "table", "exception", "sqlstate", "reason"}
            and candidate.get("stage") in ("probe", "source-table", "snapshot-validation")
            and isinstance(candidate.get("exception"), str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,70}", candidate["exception"])
            and (
                "table" not in candidate
                or (isinstance(candidate["table"], str) and candidate["table"] in TABLES)
            )
            and (
                "reason" not in candidate
                or (isinstance(candidate["reason"], str) and candidate["reason"] in SAFE_REASONS)
            )
            and (
                "sqlstate" not in candidate
                or (
                    isinstance(candidate["sqlstate"], str)
                    and re.fullmatch(r"[0-9A-Z]{5}", candidate["sqlstate"])
                )
            )
        ):
            # Probe only emits fixed stage/table/type/sqlstate/static validator
            # reasons. Never retain raw stderr (which may include SQL parameters).
            failure = candidate
        diagnostic = failed_body.get("diagnostic_source")
        if (
            failed_body.get("diagnostic_only") is True
            and failed_body.get("retention_verified") is False
            and isinstance(diagnostic, dict)
            and diagnostic.get("batch_id") == batch_id
            and diagnostic.get("owner_id") == bootstrap["operator_id"]
            and diagnostic.get("batch_scope") == "user:" + bootstrap["operator_id"]
            and diagnostic.get("source_sha256") == source_digest()
            and diagnostic.get("read_only") is True
            and isinstance(diagnostic.get("tables"), dict)
            and set(diagnostic["tables"]) == TABLES
            and len(error.stdout or b"") <= 4 * 1024 * 1024
        ):
            atomic_json(
                root / f"{prefix}.{phase}.DIAGNOSTIC_ONLY.raw.json",
                {
                    "schema_version": 1,
                    "binding": binding,
                    "diagnostic_only": True,
                    "retention_verified": False,
                    "source": diagnostic,
                    "error": failure,
                },
            )
        atomic_json(
            root / f"{prefix}.{phase}.failed.json",
            {
                "schema_version": 1,
                "binding": binding,
                "status": "failed",
                "phase": phase,
                "exit_code": error.returncode,
                "error": failure,
            },
        )
        raise
    inspect()
    try:
        if len(observed.stdout) > 4 * 1024 * 1024:
            raise ValueError("retention private evidence bound exceeded")
        value = json.loads(observed.stdout)
        verification = validate_snapshot(
            value, batch_id=batch_id, owner_id=bootstrap["operator_id"]
        )
        if (
            value.get("source_sha256") != source_digest()
            or value.get("verification") != verification
        ):
            raise ValueError("stale source or producer retention mismatch")
        tables = value["tables"]
        namespace = next(row for row in tables["namespaces"] if row["id"] == batch_id)
        if namespace["body"]["mode"] != record["mode"]:
            raise ValueError("native timeout mode differs from authoritative namespace")
        if record["result"]["run_id"] not in {row["run_id"] for row in tables["attempts"]}:
            raise ValueError("native subject does not match retained batch")
        score_ids = {row["id"] for row in tables["scores"]}
        judge_runs = {row["run_id"] for row in tables["intents"]}
        if any(
            row["id"] not in score_ids or row["judge_run_id"] not in judge_runs for row in errors
        ):
            raise ValueError("original native error audit not retained")
        # Preserve an actual comparison failure only after the fresh source has
        # passed owner, current-source, mode, native history and snapshot checks.
        # It is private diagnostic data, never a canonical proof or pass receipt.
        diagnostic_ready = True
        if phase == "after":
            verification = verify_retention(
                before, value, batch_id=batch_id, owner_id=bootstrap["operator_id"]
            )
        raw_sha = hashlib.sha256(observed.stdout).hexdigest()
        receipt = {
            "schema_version": 1,
            "binding": binding,
            "status": "protected-retention-observed"
            if phase == "before"
            else "verified-protected-retention",
            "verification": verification,
        }
        if phase == "before":
            receipt["raw_sha256"] = raw_sha
        else:
            receipt.update(
                before_sha256=before_sha,
                after_sha256=raw_sha,
                evidence={"before": before, "after": value},
                comparison_policy=COMPARISON_POLICY,
                observed_clock_transitions=scheduler_clock_evidence(before, value),
            )
    except (ValueError, TypeError, KeyError) as error:
        failure = safe_error(error, stage="host-validation")
        if diagnostic_ready:
            atomic_json(
                root / f"{prefix}.{phase}.DIAGNOSTIC_ONLY.raw.json",
                {
                    "schema_version": 1,
                    "binding": binding,
                    "diagnostic_only": True,
                    "retention_verified": False,
                    "source": value,
                    "raw_sha256": hashlib.sha256(observed.stdout).hexdigest(),
                    "error": failure,
                },
            )
        atomic_json(
            root / f"{prefix}.{phase}.failed.json",
            {
                "schema_version": 1,
                "binding": binding,
                "status": "failed",
                "phase": phase,
                "error": failure,
            },
        )
        raise
    # O_EXCL prevents another callback replacing immutable before evidence.
    descriptor = os.open(raw_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(observed.stdout)
        stream.flush()
        os.fsync(stream.fileno())
    atomic_json(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    args = parser.parse_args()
    collect(os.environ["ACCEPTANCE_EVIDENCE_DIR"], args.batch_id, args.phase)


if __name__ == "__main__":
    main()
