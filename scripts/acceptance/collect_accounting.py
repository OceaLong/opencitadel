"""Fixed host invocation of the read-only accounting probe; no service lifecycle changes."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

from scripts.acceptance.accounting_probe import source_digest
from scripts.acceptance.accounting_retention import validate_accounting
from scripts.acceptance.strict_bridge import assert_kernel_identity, atomic_json, read_json


def collect(root, batch_id, execute=subprocess.run):
    UUID(batch_id)
    root = Path(root)
    binding = read_json(root / "strict-binding.json")
    bootstrap = read_json(root / "strict-bootstrap.json")
    if (binding["run_id"], binding["project"], binding["invocation_id"]) != (
        os.environ["ACCEPTANCE_RUN_ID"],
        os.environ["ACCEPTANCE_PROJECT_ID"],
        os.environ["ACCEPTANCE_STRICT_INVOCATION_ID"],
    ):
        raise ValueError("foreign accounting collector")
    owned = []
    for path in (root / "cleanup-journal" / "pending").glob("*.json"):
        entry = read_json(path)
        action = entry.get("value", {})
        if (
            entry.get("run_id") == binding["run_id"]
            and action.get("action") == "delete-resource"
            and action.get("resource") == "evaluation-batch"
            and action.get("resource_id") == batch_id
            and action.get("retained_accounting") is True
        ):
            owned.append(entry)
    if len(owned) != 1:
        raise ValueError("exact owned accounting cleanup obligation missing")
    container = binding["kernel_container"]

    def inspect():
        value = execute(
            ["docker", "inspect", container], check=True, capture_output=True, timeout=30
        )
        assert_kernel_identity(json.loads(value.stdout)[0], binding, running=True)

    inspect()
    result = execute(
        [
            "docker",
            "exec",
            "-i",
            container,
            "/app/.venv/bin/python",
            "/acceptance-driver/accounting_probe.py",
        ],
        input=json.dumps(
            {"driver": {"binding": binding, "bootstrap": bootstrap}, "batch_id": batch_id}
        ).encode(),
        check=True,
        capture_output=True,
        timeout=45,
    )
    inspect()
    if len(result.stdout) > 1024 * 1024:
        raise ValueError("accounting evidence bound exceeded")
    value = json.loads(result.stdout)
    if value["source_sha256"] != source_digest():
        raise ValueError("stale accounting source")
    retention = validate_accounting(value, batch_id=batch_id, owner_id=bootstrap["operator_id"])
    if value["retention"] != retention:
        raise ValueError("producer accounting result mismatch")
    receipt = {
        "schema_version": 1,
        "binding": binding,
        "raw_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "evidence": value,
        "status": "retained-accounting",
    }
    raw = root / f"evaluation-lifecycle-accounting-{batch_id}.raw.json"
    raw.write_bytes(result.stdout)
    os.chmod(raw, 0o600)
    atomic_json(root / f"evaluation-lifecycle-accounting-{batch_id}.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    collect(os.environ["ACCEPTANCE_EVIDENCE_DIR"], args.batch_id)


if __name__ == "__main__":
    main()
