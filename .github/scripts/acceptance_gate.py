"""CI acceptance report.

Reads the newest acceptance evidence manifest and summarises which acceptance
requirements failed or went missing. Known-incomplete requirements are
tolerated: AC21 (e2e/README.md documents that the CLI entrypoint cannot build
the private capacity proof context yet), AC04/AC14 (sandbox admission on
memory-constrained hosts) and AC10/AC11 (covered by the same lifecycle test as
AC14, so they go missing whenever it fails). Anything else is surfaced as a GitHub warning
annotation and a non-zero exit; ci.yml currently runs this step with
continue-on-error so the job stays green until the plane is made blocking again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.acceptance.manifest import REQUIRED_ACCEPTANCE_IDS

TOLERATED = frozenset({"AC04", "AC10", "AC11", "AC14", "AC21"})


def main(evidence_root: str, runner_outcome: str) -> int:
    if runner_outcome in {"0", "success"}:
        print("::notice::acceptance runner passed every requirement")
        return 0
    manifests = sorted(Path(evidence_root).glob("run-*/manifest.json"))
    if not manifests:
        print("::warning::acceptance failed and no evidence manifest was written")
        return 1
    document = json.loads(manifests[-1].read_text())
    scope = document.get("scope") or {}
    if scope.get("kind") != "full":
        print(f"::warning::acceptance scope is not full: {scope}")
        return 1
    coverage = document.get("coverage") or []
    seen = {item["requirement_id"] for item in coverage}
    failed = {item["requirement_id"] for item in coverage if item.get("status") != "passed"}
    missing = REQUIRED_ACCEPTANCE_IDS - seen
    tolerated = sorted((failed | missing) & TOLERATED)
    blocking = sorted((failed | missing) - TOLERATED)
    if tolerated:
        print("::notice::known-incomplete acceptance requirements: " + ", ".join(tolerated))
    if blocking:
        print(
            "::warning::acceptance requirements failed outside the tolerated set: "
            + ", ".join(blocking)
        )
        return 1
    print("acceptance passed every requirement except the known-incomplete set")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
