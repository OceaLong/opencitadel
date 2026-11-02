"""Focused browser proof with exact collection; never a release acceptance manifest."""

import json
from dataclasses import asdict
from datetime import UTC, datetime

from scripts.acceptance.manifest import sha256_file, write_manifest_atomic

EXPECTED_TESTS = {
    "bootstrap deterministic inference through the public control plane": "bootstrap",
    "retained conversation exposes real compact steps and bounded context details": "phase2-context",
    "remove acceptance product resources through public APIs": "cleanup",
    "version availability follows actual worker expiry and recovery": "phase2-context",
}


def browser_tests(document):
    rows = []

    def visit(suites):
        for suite in suites:
            for spec in suite.get("specs", []):
                rows.extend(
                    {
                        "title": spec["title"],
                        "project": test["projectName"],
                        "status": (test.get("results") or [{}])[-1].get("status", "not_run"),
                        "attempts": len(test.get("results", [])),
                        "expected_status": test.get("expectedStatus"),
                    }
                    for test in spec.get("tests", [])
                )
            visit(suite.get("suites", []))

    visit(document.get("suites", []))
    return rows


def validate_browser(document):
    rows = browser_tests(document)
    errors = []
    if len(rows) != len(EXPECTED_TESTS):
        errors.append(
            "Phase 2 context must collect exactly its context browser cases plus bootstrap and cleanup"
        )
    for title, project in EXPECTED_TESTS.items():
        matches = [row for row in rows if row["title"] == title and row["project"] == project]
        if len(matches) != 1 or any(
            row["status"] != "passed" or row["attempts"] != 1 or row["expected_status"] != "passed"
            for row in matches
        ):
            errors.append(f"Phase 2 context browser node must pass once: {title}")
    if document.get("errors"):
        errors.append("Playwright reported a global failure")
    return rows, errors


def write_phase2_evidence(*, root, config, failure_reason, **values):
    errors = []
    native = root / "playwright/native-results.json"
    if native.is_file():
        rows, errors = validate_browser(json.loads(native.read_text()))
    else:
        rows, errors = [], ["Phase 2 context native browser evidence is absent"]
    residue = values["residue"]
    sandboxes = values["sandboxes"]
    if not config.disposable or not residue.empty or sandboxes.created != sandboxes.drained:
        errors.append("Phase 2 context owned resources must be drained with disposable cleanup")
    artifacts = []
    for name in (
        "playwright/native-results.json",
        "playwright/junit.xml",
        "logs/stack.log",
    ):
        path = root / name
        if not path.is_file():
            errors.append(f"Phase 2 context artifact absent: {name}")
        else:
            artifacts.append(
                {"path": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            )
    write_manifest_atomic(
        root / "phase2-context-manifest.json",
        {
            "schema_version": 1,
            "scope": "phase2-context-browser",
            "requirements": ["P2-context-ui"],
            "result": "failed" if failure_reason or errors else "passed",
            "failure_reason": failure_reason,
            "validation_errors": errors,
            "started_at": values["started_at"],
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_ms": values["duration_ms"],
            "project": config.project_name,
            "run_id": config.run_id,
            "git": asdict(values["git"]),
            "images": asdict(values["images"]),
            "migration": asdict(values["migration"]),
            "services": [asdict(value) for value in values["services"]],
            "sandboxes": asdict(sandboxes),
            "residue": asdict(residue),
            "tests": rows,
            "artifacts": artifacts,
        },
    )
    return errors
