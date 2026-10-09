"""Shared capacity v3 facade. Ordinary acceptance consumes; it never measures.

The producer must call derive_capacity_report on actual role exports. A passing
synthetic transcript is unit evidence only, never runtime capacity acceptance.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from scripts.acceptance.capacity_completion import resource_statistics
from scripts.acceptance.capacity_derive import budget_errors, derive_summary, percentile
from scripts.acceptance.capacity_io import (
    MAX_ARTIFACT_BYTES,
    MAX_REPORT_BYTES,
    copy_artifacts,
    read_bounded,
    strict_json,
    write_relative,
)
from scripts.acceptance.capacity_models import Artifact, Binding, Report
from scripts.acceptance.capacity_package import PackageSession
from scripts.acceptance.capacity_physical import require, unique

BUDGETS_MS = {
    "warm": {
        "first_screen": 2000,
        "switch": 200,
        "history": 1000,
        "analysis": 2000,
        "matrix": 2000,
        "live_visible": 2000,
    },
    "cold": {"first_screen": 4000, "history": 2000, "analysis": 5000},
}
FIXTURE_COUNTS = {
    "runs": 100000,
    "formal_events": 10000000,
    "hot_runs": 10,
    "hot_run_events": 10000,
    "cases": 1000,
    "configurations": 5,
    "repetitions": 1,
    "results": 5000,
    "window_days": 90,
}


def derive_capacity_report(
    *,
    artifacts: list[dict],
    binding: dict,
    completed_binding: dict,
    root: Path,
    started_at: str,
    finished_at: str,
    proof_context=None,
) -> dict:
    """Producer entrypoint: derive arrays from strict actual safe role records.

    This does not fabricate measurement records or approve provenance. Invalid
    roles/joins raise ValueError; budget failures remain in numeric output and
    validate_capacity_report reports every violated budget.
    """
    proof_context = _proof_context(proof_context)
    binding = Binding.model_validate(binding).model_dump()
    with PackageSession(
        [Artifact.model_validate(a) for a in artifacts],
        root,
        resources=proof_context.public_resources(),
    ) as package:
        roles = package.roles
        proof_context.validate(roles, package=package)
        protocol = roles["protocol"]
        summary = derive_summary(roles, binding)
        return {
            "schema_version": 4,
            "binding": binding,
            "completed_binding": completed_binding,
            "started_at": started_at,
            "finished_at": finished_at,
            "fixture_counts": dict(FIXTURE_COUNTS),
            "attempt_id": protocol.attempt_id,
            "protocol_id": protocol.protocol_id,
            "protocol_digest": next(a["sha256"] for a in artifacts if a["role"] == "protocol"),
            "artifacts": artifacts,
            **summary,
        }


def validate_capacity_report(
    report: object, expected_binding: dict, root: Path, *, proof_context=None
) -> list[str]:
    """Fail closed on all untyped, legacy, incomplete or inconsistent evidence."""
    errors = []
    try:
        proof_context = _proof_context(proof_context)
        parsed = Report.model_validate(report)
        binding = Binding.model_validate(expected_binding).model_dump()
        require(bool(binding["images"]), "runner binding missing images")
        require(parsed.binding.model_dump() == binding, "capacity build/fixture binding mismatch")
        require(
            parsed.completed_binding.model_dump() == binding,
            "capacity build changed during measurement",
        )
        start, end = (
            datetime.fromisoformat(parsed.started_at),
            datetime.fromisoformat(parsed.finished_at),
        )
        require(
            start.tzinfo is not None and end.tzinfo is not None and end > start,
            "invalid capacity interval",
        )
        require(
            parsed.fixture_counts == FIXTURE_COUNTS, "capacity standard fixture counts mismatch"
        )
        with PackageSession(
            parsed.artifacts, root, resources=proof_context.public_resources()
        ) as package:
            roles = package.roles
            proof_context.validate(roles, package=package)
            fixture = next(a for a in parsed.artifacts if a.role == "fixture")
            require(
                fixture.sha256 == binding["fixture_manifest_digest"],
                "fixture manifest digest mismatch",
            )
            protocol_artifact = next(a for a in parsed.artifacts if a.role == "protocol")
            require(parsed.protocol_digest == protocol_artifact.sha256, "protocol digest mismatch")
            require(
                (parsed.attempt_id, parsed.protocol_id)
                == (roles["protocol"].attempt_id, roles["protocol"].protocol_id),
                "report attempt/protocol mismatch",
            )
            derived = derive_summary(roles, binding)
            actual = parsed.model_dump()
            require(
                all(actual[key] == value for key, value in derived.items()),
                "raw measurement samples differ from report",
            )
            errors.extend(budget_errors(derived))
            plans = unique(roles["protocol"].samples, "sample_id")
            samples = unique(roles["measurements"].samples, "sample_id")
            windows = unique(roles["workload"].windows, "window_id")
            for trace in roles["measurements"].resources:
                plan = plans[trace.sample_id]
                # Use the same fixed-slot population as the pooled derivation;
                # retained boundary frames must neither dilute nor inflate p95.
                selected = resource_statistics(
                    plan, samples[trace.sample_id], trace, windows[plan.window_id]
                )
                if percentile([f.interval_ms for f in selected.frames], 0.95) > 33:
                    errors.append(f"frame p95 exceeds 33ms for {trace.sample_id}")
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        # Pydantic errors must not echo arbitrary untrusted input/secret values.
        from pydantic import ValidationError

        if isinstance(error, ValidationError):
            errors.append(
                "invalid capacity schema: "
                + "; ".join(e["type"] for e in error.errors(include_input=False)[:12])
            )
        else:
            from scripts.execution_capacity.offline_context import PrivateProofError

            errors.append(
                str(error)
                if isinstance(error, PrivateProofError)
                else "invalid capacity evidence: " + type(error).__name__
            )
    return errors


def prepare_capacity_evidence(
    *,
    report_path: Path | None,
    fixture_path: Path | None,
    evidence_root: Path,
    build: dict,
    run_id: str,
    project: str,
    proof_context=None,
) -> dict:
    """Retain bounded safe bytes, then rederive the retained package independently."""
    from scripts.acceptance.manifest import write_manifest_atomic

    destination = evidence_root / "capacity"
    evidence_root.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": 2,
        "run_id": run_id,
        "project": project,
        "errors": [],
        "binding": build,
    }
    try:
        proof_context = _proof_context(proof_context)
        if report_path is None or fixture_path is None:
            raise ValueError(
                "AC21 requires ACCEPTANCE_CAPACITY_REPORT and ACCEPTANCE_CAPACITY_FIXTURE_MANIFEST"
            )
        original = read_bounded(report_path, MAX_REPORT_BYTES)
        report = strict_json(original)
        binding = {
            **build,
            "fixture_manifest_digest": hashlib.sha256(
                read_bounded(fixture_path, MAX_ARTIFACT_BYTES)
            ).hexdigest(),
        }
        receipt["binding"] = binding
        receipt["errors"] = validate_capacity_report(
            report, binding, report_path.parent, proof_context=proof_context
        )
        if not receipt["errors"]:
            from scripts.execution_capacity.proof_copy import private_container

            private_root = private_container(evidence_root, "capacity-private")
            copied_context = proof_context.copy(private_root / "originals")
            parsed = Report.model_validate(report)
            copy_artifacts(parsed.artifacts, report_path.parent, destination)
            if read_bounded(report_path, MAX_REPORT_BYTES) != original:
                raise ValueError("capacity report changed during copy")
            retained = destination / "report.json"
            if retained.is_symlink():
                raise ValueError("symlink retained report")
            write_relative(destination, "report.json", original)
            copied = strict_json(read_bounded(retained, MAX_REPORT_BYTES))
            receipt["errors"] = validate_capacity_report(
                copied, binding, destination, proof_context=copied_context
            )
            if not receipt["errors"]:
                observed = {
                    f"{mode}.{op}": values
                    for mode in ("warm", "cold")
                    for op, values in copied[mode].items()
                }
                observed.update(
                    {
                        f"step_capacity.{mode}.{op}": values
                        for mode in ("warm", "cold")
                        for op, values in copied["step_capacity"][mode].items()
                    }
                )
                observed.update(
                    {f"latency.{key}": values for key, values in copied["latency"].items()}
                )
                receipt["percentiles_ms"] = {
                    key: {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}
                    for key, values in observed.items()
                }
                receipt["percentiles_ms"]["frames"] = {
                    "p50": copied["frames"]["p50_ms"],
                    "p95": copied["frames"]["p95_ms"],
                }
            receipt["report_sha256"] = hashlib.sha256(original).hexdigest()
    except (OSError, ValueError, TypeError, KeyError) as error:
        from scripts.execution_capacity.offline_context import PrivateProofError

        receipt["errors"] = [
            "capacity handoff: "
            + (str(error) if isinstance(error, PrivateProofError) else type(error).__name__)
        ]
    write_manifest_atomic(evidence_root / "capacity-validation.json", receipt)
    return receipt


def _proof_context(value):
    from scripts.execution_capacity.offline_context import OfflineProofContext, PrivateProofError

    if type(value) is not OfflineProofContext:
        raise PrivateProofError("private C2c original context required")
    return value
