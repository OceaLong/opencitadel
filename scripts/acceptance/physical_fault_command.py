"""Fixed argv/stdin command, executed only inside verified acceptance kernel."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).parent))
from physical_fault_authority import current, pending, prepare
from physical_fault_runtime import cleanup_marker
from physical_faults import (
    FaultBusy,
    FaultControl,
    FaultError,
    public_binding,
    source_digest,
    validate_fault_rows,
)

# Only these reviewed literal failures may cross the private command boundary.
_AUDITED_FAULT_REASONS = {
    "actual adapter slot changed": "actual_adapter_slot_changed",
    "actual approved call-started claim absent after projection wait": "actual_approved_call_started_claim_absent_after_projection_wait",
    "actual dispatch facts absent": "actual_dispatch_facts_absent",
    "actual model decision identity ambiguous": "actual_model_decision_identity_ambiguous",
    "actual physical send absent": "actual_physical_send_absent",
    "actual request differs from armed model decision": "actual_request_differs_from_armed_model_decision",
    "actual unknown settlement/convergence absent": "actual_unknown_settlement_convergence_absent",
    "approval/authority changed during positive read": "approval_authority_changed_during_positive_read",
    "audit duplicate": "audit_duplicate",
    "audit gap/restart": "audit_gap_restart",
    "audit time order invalid": "audit_time_order_invalid",
    "audit unmatched end": "audit_unmatched_end",
    "authority token changed": "authority_token_changed",
    "claim generation changed after trigger": "claim_generation_changed_after_trigger",
    "contending arm changed; retry not authorized": "contending_arm_changed_retry_not_authorized",
    "disarm identity mismatch": "disarm_identity_mismatch",
    "dispatch outside actual handler": "dispatch_outside_actual_handler",
    "duplicate fault invocation": "duplicate_fault_invocation",
    "fault boot changed": "fault_boot_changed",
    "fault changed": "fault_changed",
    "fault context inherited by a different task": "fault_context_inherited_by_a_different_task",
    "fault evidence overflow": "fault_evidence_overflow",
    "fault expired": "fault_expired",
    "fault incomplete": "fault_incomplete",
    "fault inflight": "fault_inflight",
    "fault journal incomplete, reordered or foreign": "fault_journal_incomplete_reordered_or_foreign",
    "fault source changed": "fault_source_changed",
    "fault/dispatch proof incomplete": "fault_dispatch_proof_incomplete",
    "faulted first object read consumed recording": "faulted_first_object_read_consumed_recording",
    "foreign audit boot": "foreign_audit_boot",
    "foreign fault action": "foreign_fault_action",
    "foreign fault deployment": "foreign_fault_deployment",
    "foreign fault journal": "foreign_fault_journal",
    "foreign or expired active control retained": "foreign_or_expired_active_control_retained",
    "formal run integrity mismatch": "formal_run_integrity_mismatch",
    "host source changed": "host_source_changed",
    "incomplete audit": "incomplete_audit",
    "inflight triggered or poisoned control retained": "inflight_triggered_or_poisoned_control_retained",
    "invalid active fault control retained": "invalid_active_fault_control_retained",
    "invalid active fault identity retained": "invalid_active_fault_identity_retained",
    "invalid fault lifetime/kind": "invalid_fault_lifetime_kind",
    "later marker witness is not exactly one write": "later_marker_witness_is_not_exactly_one_write",
    "operator no longer active": "operator_no_longer_active",
    "owned marker absence unconfirmed": "owned_marker_absence_unconfirmed",
    "owned marker already exists or absence unverified": "owned_marker_already_exists_or_absence_unverified",
    "owned marker deletion unconfirmed": "owned_marker_deletion_unconfirmed",
    "owned pending approval absent": "owned_pending_approval_absent",
    "owned persisted result absent": "owned_persisted_result_absent",
    "owned physical write fixture mismatch": "owned_physical_write_fixture_mismatch",
    "owned sandbox identity changed": "owned_sandbox_identity_changed",
    "owned sandbox unavailable": "owned_sandbox_unavailable",
    "owned sandbox unavailable before arm": "owned_sandbox_unavailable_before_arm",
    "pending approval drift": "pending_approval_drift",
    "persisted model decision digest mismatch": "persisted_model_decision_digest_mismatch",
    "persisted request differs from actual worker request": "persisted_request_differs_from_actual_worker_request",
    "physical marker witness differs from exactly one write": "physical_marker_witness_differs_from_exactly_one_write",
    "physical request changed": "physical_request_changed",
    "physical sandbox/arguments changed": "physical_sandbox_arguments_changed",
    "physical unknown requires normal session": "physical_unknown_requires_normal_session",
    "physical write has no completed exit0 receipt": "physical_write_has_no_completed_exit0_receipt",
    "positive real recorded object read failed": "positive_real_recorded_object_read_failed",
    "recorded arguments differ": "recorded_arguments_differ",
    "recorded slot ambiguous": "recorded_slot_ambiguous",
    "recording admission changed": "recording_admission_changed",
    "recording authority changed at adapter": "recording_authority_changed_at_adapter",
    "recording binding changed": "recording_binding_changed",
    "replay mismatch was not persisted": "replay_mismatch_was_not_persisted",
    "selected call is not approved shell": "selected_call_is_not_approved_shell",
    "selected handler inflight or restarted": "selected_handler_inflight_or_restarted",
    "selected run dispatched another tool identity": "selected_run_dispatched_another_tool_identity",
    "unknown audit event": "unknown_audit_event",
    "unknown dispatch kind": "unknown_dispatch_kind",
    "unknown was retried with new generation": "unknown_was_retried_with_new_generation",
    "unsafe dispatch audit": "unsafe_dispatch_audit",
    "unsafe fault directory": "unsafe_fault_directory",
    "unsafe fault file": "unsafe_fault_file",
    "unsupported action": "unsupported_action",
    "unsupported fault schema": "unsupported_fault_schema",
}


def failure_code(error):
    reason = _AUDITED_FAULT_REASONS.get(str(error)) if type(error) is FaultError else None
    return f"FaultError:{reason}" if reason else type(error).__name__


def audit(arm=None):
    path = Path("/tmp/acceptance-dispatch.ndjson")
    if path.is_symlink() or path.stat().st_size > 16777216:
        raise FaultError("unsafe dispatch audit")
    raw = path.read_bytes()
    if not raw.endswith(b"\n"):
        raise FaultError("incomplete audit")
    rows = [json.loads(line) for line in raw.splitlines()]
    boot = rows[0]
    if (
        boot["event"] != "boot"
        or boot["source_sha256"] != source_digest()
        or boot["project"] != os.environ["ACCEPTANCE_PROJECT_ID"]
        or boot["run_id"] != os.environ["ACCEPTANCE_RUN_ID"]
    ):
        raise FaultError("foreign audit boot")
    seen = set()
    last_time = -1
    opened, counts = {}, {"handler": 0, "catalog": 0, "replay": 0}
    for index, row in enumerate(rows):
        if row["sequence"] != index or any(
            row[k] != boot[k] for k in ("boot_id", "source_sha256", "project", "run_id")
        ):
            raise FaultError("audit gap/restart")
        if type(row.get("monotonic_ns")) is not int or row["monotonic_ns"] < last_time:
            raise FaultError("audit time order invalid")
        last_time = row["monotonic_ns"]
        if index == 0:
            continue
        if row["event"] == "begin":
            if row["call_id"] in seen:
                raise FaultError("audit duplicate")
            seen.add(row["call_id"])
            if row["kind"] not in counts:
                raise FaultError("unknown dispatch kind")
            if row["kind"] != "handler" and not any(
                entry["kind"] == "handler"
                and all(
                    entry[key] == row[key]
                    for key in ("execution_run_id", "activity_id", "generation", "claim_generation")
                )
                for entry in opened.values()
            ):
                raise FaultError("dispatch outside actual handler")
            opened[row["call_id"]] = row
            if arm and row["execution_run_id"] == arm["execution_run_id"]:
                if (
                    row["activity_id"] != arm["activity_id"]
                    or row["generation"] != arm["generation"]
                    or row["owner_user_id"] != arm["owner_user_id"]
                    or row["team_id"] is not None
                ):
                    raise FaultError("selected run dispatched another tool identity")
                counts[row["kind"]] += 1
        elif row["event"] == "end":
            before = opened.pop(row["call_id"], None)
            if before is None or any(
                before[k] != row[k]
                for k in (
                    "activity_id",
                    "generation",
                    "claim_generation",
                    "kind",
                    "execution_run_id",
                )
            ):
                raise FaultError("audit unmatched end")
        else:
            raise FaultError("unknown audit event")
    if arm and (
        arm["boot_id"] != boot["boot_id"]
        or any(row["execution_run_id"] == arm["execution_run_id"] for row in opened.values())
    ):
        raise FaultError("selected handler inflight or restarted")
    return boot, counts, len(rows) - 1


async def inspect_result(shared, arm, *, cleanup=False):
    from app.domain.execution.run import RunState
    from app.infrastructure.execution.models import ExecutionActivityTaskORM as Task
    from app.infrastructure.execution.models import ExecutionRunProjectionORM as Run

    _, scope, auth = await current(shared.uow_factory, arm["owner_user_id"])
    async with shared.uow_factory(auth) as work:
        row = await work.db_session.get(Run, UUID(arm["execution_run_id"]))
        task = await work.db_session.get(Task, UUID(arm["activity_id"]))
        if (
            row is None
            or task is None
            or row.owner_user_id != arm["owner_user_id"]
            or row.team_id is not None
        ):
            raise FaultError("owned persisted result absent")
        state = RunState.model_validate(row.state)
        if (
            task.request_generation != arm["generation"]
            or state.retry_generation != arm["generation"]
        ):
            raise FaultError("unknown was retried with new generation")
        proof = {
            "task_status": task.status,
            "failure_code": task.failure_code,
            "generation": task.request_generation,
            "claim_generation": task.claim_generation,
            "run_status": row.status,
            "stream_version": state.stream_version,
        }
        if arm["kind"] == "recorded_object_missing":
            if task.status != "failed" or task.failure_code != "REPLAY_MISMATCH":
                raise FaultError("replay mismatch was not persisted")
            consumed = await work.evaluation_recording.consumed(
                scope, UUID(arm["execution_run_id"]), UUID(arm["activity_id"])
            )
            if consumed is not None:
                raise FaultError("faulted first object read consumed recording")
            proof["consumed"] = False
        else:
            if (
                task.status != "unknown"
                or task.failure_code != "NON_IDEMPOTENT_OUTCOME_UNKNOWN"
                or (UUID(arm["activity_id"]), "unknown", arm["generation"])
                not in state.settled_activities
                or row.status != "failed"
            ):
                raise FaultError("actual unknown settlement/convergence absent")
            session = await work.session.get_by_id(arm["source_entity_id"], scope=scope)
            if session is None or session.sandbox_id != arm["sandbox_id"]:
                raise FaultError("owned sandbox identity changed")
            sandbox = await shared.sandbox_factory.get(arm["sandbox_id"])
            if sandbox is None or sandbox.id != arm["sandbox_id"]:
                raise FaultError("owned sandbox unavailable")
            try:
                witness = await sandbox.read_file(arm["marker_path"], max_length=128)
                if not witness.success or witness.data.get("content") != "owned-write\n":
                    raise FaultError("later marker witness is not exactly one write")
                proof["marker_lines"] = 1
                if cleanup:
                    await cleanup_marker(sandbox, arm["marker_path"])
                    proof["marker_deleted"] = True
            finally:
                await sandbox.client.aclose()
        return proof


async def retryable_busy(control, shared, data, boot):
    expected = {
        "runner_project": data["project"],
        "runner_run": data["run"],
        **{
            key: data[key]
            for key in (
                "invocation_id",
                "binding_sha256",
                "kernel_container",
                "source_sha256",
                "owner_user_id",
            )
        },
        "boot_id": boot["boot_id"],
    }
    if not control.ordinary_busy(expected):
        return False
    # A competing prepare/arm may have taken time; refresh the contender's
    # actual current pending approval, then reclassify under the file lock.
    await pending(
        shared.uow_factory, data["owner_user_id"], data["execution_run_id"], data["activity_id"]
    )
    return control.ordinary_busy(expected)


async def execute(data):
    from app.composition.resources import open_process_resources
    from app.composition.shared import build_shared_services
    from app.composition.tasks import TaskSupervisor
    from app.runtime_role import ProcessRole
    from core.config import load_deployment_settings

    control = FaultControl()
    settings = load_deployment_settings()
    if (
        settings.env != "test"
        or not settings.evaluation_acceptance_enabled
        or any(
            settings.sandbox_labels.get("com.opencitadel.acceptance." + key) != data[key]
            for key in ("project", "run")
        )
    ):
        raise FaultError("foreign fault deployment")
    boot, _, _ = audit()
    if data["source_sha256"] != boot["source_sha256"]:
        raise FaultError("host source changed")
    async with open_process_resources(settings, ProcessRole.EXECUTION_KERNEL) as resources:
        supervisor = TaskSupervisor(shutdown_timeout_seconds=settings.shutdown_timeout_seconds)
        try:
            shared = build_shared_services(resources, supervisor=supervisor)
            await shared.runtime_policy_reader.initialize()
            action = data["action"]
            if action == "arm":
                if await retryable_busy(control, shared, data, boot):
                    return {"busy": True}
                for key in ("execution_run_id", "activity_id"):
                    UUID(data[key])
                value = await prepare(
                    shared,
                    data["owner_user_id"],
                    data["execution_run_id"],
                    data["activity_id"],
                    data["kind"],
                    data.get("version_id"),
                )
                value.update(
                    schema_version=1,
                    runner_project=data["project"],
                    runner_run=data["run"],
                    invocation_id=data["invocation_id"],
                    binding_sha256=data["binding_sha256"],
                    kernel_container=data["kernel_container"],
                    kind=data["kind"],
                    fault_id=str(uuid4()),
                    boot_id=boot["boot_id"],
                    source_sha256=source_digest(),
                    expires_ns=time.monotonic_ns() + 300_000_000_000,
                )
                try:
                    control.arm(value)
                except FaultBusy:
                    if not await retryable_busy(control, shared, data, boot):
                        raise FaultError("contending arm changed; retry not authorized") from None
                    return {"busy": True}
                return {
                    key: value[key]
                    for key in (
                        "fault_id",
                        "boot_id",
                        "source_sha256",
                        "kind",
                        "execution_run_id",
                        "activity_id",
                        "generation",
                    )
                }
            value = control.read("arm.json")
            if (
                value is None
                or value["fault_id"] != data["fault_id"]
                or value["owner_user_id"] != data["owner_user_id"]
                or value["invocation_id"] != data["invocation_id"]
                or value["binding_sha256"] != data["binding_sha256"]
                or value["kernel_container"] != data["kernel_container"]
            ):
                raise FaultError("foreign fault action")
            _, counts, sequence = audit(value)
            if action == "cancel":
                rows = control.disarm(value["fault_id"], require_complete=False)
                return {
                    "fault_id": value["fault_id"],
                    "cleanup": "control removed; runtime/marker obligations unresolved",
                    "journal": rows,
                    "obligations": {
                        "status": "pending",
                        "session_id": value.get("source_entity_id"),
                        "sandbox_id": value.get("sandbox_id"),
                        "execution_run_id": value["execution_run_id"],
                        "activity_id": value["activity_id"],
                    },
                }
            rows = control.rows(value)
            validate_fault_rows(value, rows)
            events = [r["event"] for r in rows]
            required = ["arm", "enter", "trigger", "exit"]
            required += (
                ["mismatch"]
                if value["kind"] == "recorded_object_missing"
                else ["physical_send", "receipt"]
            )
            if (
                any(events.count(event) != 1 for event in required)
                or "duplicate" in events
                or counts
                != {
                    "handler": 1,
                    "catalog": 0 if value["kind"] == "recorded_object_missing" else 1,
                    "replay": 1 if value["kind"] == "recorded_object_missing" else 0,
                }
            ):
                raise FaultError("fault/dispatch proof incomplete")
            proof = await inspect_result(shared, value, cleanup=action == "disarm")
            if proof["claim_generation"] != next(
                r["claim_generation"] for r in rows if r["event"] == "enter"
            ):
                raise FaultError("claim generation changed after trigger")
            if action == "disarm":
                rows = control.disarm(value["fault_id"], require_complete=True)
            elif action != "snapshot":
                raise FaultError("unsupported action")
            return {
                "fault_id": value["fault_id"],
                "boot_id": value["boot_id"],
                "source_sha256": value["source_sha256"],
                "execution_run_id": value["execution_run_id"],
                "activity_id": value["activity_id"],
                "authority": public_binding(value),
                "observation": proof,
                "counts": counts,
                "last_sequence": sequence,
                "journal": rows,
                "cleanup": "control removed" if action == "disarm" else "pending",
            }
        finally:
            await supervisor.stop()


if __name__ == "__main__":
    try:
        data = json.loads(sys.stdin.buffer.read(16385))
        result = asyncio.run(execute(data))
        print(json.dumps(result, sort_keys=True))
    except BaseException as error:  # noqa: BLE001 - sanitize private runtime failures, including cancellation
        # Do not leak raw keys, private results, DB payloads or credentials.
        print(
            "physical fault action failed: "
            + failure_code(error)
            + "; evidence/cleanup unresolved",
            file=sys.stderr,
        )
        sys.exit(1)
