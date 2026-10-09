"""Explicit host collector for the exact runner-owned observed kernel; no watcher."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

from scripts.acceptance.dispatch_audit import MAX_BYTES, validate_window
from scripts.acceptance.physical_faults import source_digest
from scripts.acceptance.strict_bridge import assert_kernel_identity, atomic_json, read_json

SAFE_FAILURE_REASONS = frozenset(
    {
        "audit boot and distinct positive controls required",
        "audit dropped, restarted or foreign",
        "invalid audit ordering",
        "audit identity incomplete",
        "audit scope mismatch",
        "duplicate interval",
        "dispatch interval has no matching active handler",
        "unmatched audit end",
        "invalid replay mismatch observation",
        "unclosed audit interval",
        "source/isolated positive control absent",
        "replay lacked observation or entered real catalog",
        "audit snapshot oversized/truncated",
    }
)


def collect(*, root, replay_run, source_run, isolated_run, execute=subprocess.run):
    root = Path(root)
    binding = read_json(root / "strict-binding.json")
    bootstrap = read_json(root / "strict-bootstrap.json")
    if (binding["run_id"], binding["project"], binding["invocation_id"]) != (
        os.environ["ACCEPTANCE_RUN_ID"],
        os.environ["ACCEPTANCE_PROJECT_ID"],
        os.environ["ACCEPTANCE_STRICT_INVOCATION_ID"],
    ):
        raise ValueError("foreign collector invocation")
    for value in (replay_run, source_run, isolated_run):
        UUID(value)
    if len({replay_run, source_run, isolated_run}) != 3:
        raise ValueError("distinct positive and replay run identities required")
    container = binding["kernel_container"]

    def run(args):
        return execute(args, check=True, capture_output=True, timeout=30).stdout

    def inspect():
        assert_kernel_identity(
            json.loads(run(["docker", "inspect", container]))[0], binding, running=True
        )

    inspect()
    # Fixed code/path only. No browser-provided shell, module, container or file.
    raw = run(
        [
            "docker",
            "exec",
            container,
            "/app/.venv/bin/python",
            "-c",
            "from pathlib import Path; import sys; p=Path('/tmp/acceptance-dispatch.ndjson'); assert not p.is_symlink() and p.stat().st_size <= 16777216; sys.stdout.buffer.write(p.read_bytes())",
        ]
    )
    inspect()
    digest = source_digest()
    observer_binding = {
        "project": binding["project"],
        "run_id": binding["run_id"],
        "source_sha256": digest,
    }
    owned_source = False
    try:
        if len(raw) > MAX_BYTES or not raw.endswith(b"\n"):
            raise ValueError("audit snapshot oversized/truncated")
        records = [json.loads(line) for line in raw.splitlines()]
        owned_source = (
            bool(records)
            and isinstance(records[0], dict)
            and (
                records[0].get("event") == "boot"
                and all(records[0].get(key) == value for key, value in observer_binding.items())
            )
        )
        result = validate_window(
            records,
            observer_binding,
            replay_run=replay_run,
            positive_runs=[source_run, isolated_run],
            owner_user_id=bootstrap["operator_id"],
        )
    except (ValueError, TypeError, KeyError) as error:
        failure = {"stage": "audit-validation", "exception": type(error).__name__}
        if str(error) in SAFE_FAILURE_REASONS:
            failure["reason"] = str(error)
        # A fresh exact inspected kernel and matching observer/source boot permit
        # private diagnostic bytes. They never become a zero-call pass proof.
        if owned_source:
            diagnostic = root / f"dispatch-{replay_run}.DIAGNOSTIC_ONLY.ndjson"
            descriptor = os.open(diagnostic, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
        atomic_json(
            root / f"dispatch-{replay_run}.failed.json",
            {
                "schema_version": 1,
                "binding": binding,
                "observer_binding": observer_binding,
                "diagnostic_only": True,
                "dispatch_verified": False,
                "error": failure,
            },
        )
        raise
    raw_path = root / f"dispatch-{replay_run}.ndjson"
    raw_path.write_bytes(raw)
    os.chmod(raw_path, 0o600)
    receipt = {
        "schema_version": 1,
        "binding": binding,
        "observer_binding": observer_binding,
        "replay_run_id": replay_run,
        "source_run_id": source_run,
        "isolated_run_id": isolated_run,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "observation": result,
    }
    atomic_json(root / f"dispatch-{replay_run}.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    for name in ("replay-run", "source-run", "isolated-run"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    collect(
        root=os.environ["ACCEPTANCE_EVIDENCE_DIR"],
        replay_run=args.replay_run,
        source_run=args.source_run,
        isolated_run=args.isolated_run,
    )


if __name__ == "__main__":
    main()
