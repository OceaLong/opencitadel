"""Read-only current authority and persisted request checks for private faults."""

import asyncio
from uuid import UUID

try:
    from .physical_faults import FaultError, digest
except ImportError:
    from physical_faults import FaultError, digest


def verify_request(arm, request, context):
    if (
        (
            str(context.run.run_id),
            str(context.activity_id),
            context.generation,
            context.owner_user_id,
            context.team_id,
        )
        != (
            arm["execution_run_id"],
            arm["activity_id"],
            arm["generation"],
            arm["owner_user_id"],
            None,
        )
        or request.activity_id != context.activity_id
        or request.generation != context.generation
        or context.claim_generation < 1
        or digest(request.input_payload.get("tool_call")) != arm["call_digest"]
        or request.input_payload.get("catalog_fingerprint") != arm["catalog_fingerprint"]
    ):
        raise FaultError("actual request differs from armed model decision")


async def current(factory, owner):
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.models.scope import OwnerScope, Principal

    async with factory(AuthorizationContext.system("execution-kernel")) as work:
        user = await work.user.get_by_id(owner)
        if user is None or not user.is_active:
            raise FaultError("operator no longer active")
        team_roles = {}
        for team in await work.team.list_for_user(owner):
            member = await work.team.get_member(team.id, owner)
            if member:
                team_roles[team.id] = member.role
        principal = Principal(
            user_id=user.id,
            global_role=user.global_role,
            token_version=user.token_version,
            team_roles=team_roles,
        )
    scope = OwnerScope.personal(owner)
    return principal, scope, AuthorizationContext.for_principal(principal, scope=scope)


async def pending(factory, owner, run_id, activity_id):
    """The tool request does not yet exist: derive its ID from actual model fact."""
    from sqlalchemy import select

    from app.application.execution.decisions.base import activity_identity, approval_identity
    from app.domain.execution.run import RunState, decision_data_digest
    from app.domain.execution.serialization import canonical_state_hash
    from app.infrastructure.execution.models import ExecutionActivityTaskORM as Task
    from app.infrastructure.execution.models import ExecutionApprovalProjectionORM as Approval
    from app.infrastructure.execution.models import ExecutionRunProjectionORM as Run

    principal, scope, authorization = await current(factory, owner)
    async with factory(authorization) as work:
        row = await work.db_session.get(Run, UUID(run_id))
        approval = await work.db_session.scalar(
            select(Approval).where(
                Approval.run_id == UUID(run_id),
                Approval.subject_activity_id == UUID(activity_id),
                Approval.status == "pending",
            )
        )
        if row is None or row.owner_user_id != owner or row.team_id is not None or approval is None:
            raise FaultError("owned pending approval absent")
        state = RunState.model_validate(row.state)
        if canonical_state_hash(state) != row.state_hash:
            raise FaultError("formal run integrity mismatch")
        if (
            state.pending_approval_activity_id != UUID(activity_id)
            or state.pending_approval_id != approval.approval_id
        ):
            raise FaultError("pending approval drift")
        models = (
            await work.db_session.scalars(
                select(Task).where(Task.run_id == run_id, Task.status == "succeeded")
            )
        ).all()
        matches = []
        for model in models:
            for round_index in range(64):
                if model.activity_id != activity_identity(state, f"model:{round_index}"):
                    continue
                for ordinal, call in enumerate(model.decision_payload.get("tool_calls", [])):
                    key = f"tool:{round_index}:{ordinal}:{call['call_id']}"
                    if (
                        activity_identity(state, key) == UUID(activity_id)
                        and approval_identity(state, key) == approval.approval_id
                    ):
                        if not call.get("requires_approval") or call.get("name") != "shell_execute":
                            raise FaultError("selected call is not approved shell")
                        matches.append((model, call, round_index, ordinal))
        if len(matches) != 1:
            raise FaultError("actual model decision identity ambiguous")
        model, call, round_index, ordinal = matches[0]
        decisions = [
            entry
            for entry in state.activity_results
            if entry[0] == model.activity_id and entry[1] == state.retry_generation
        ]
        if len(decisions) != 1 or decision_data_digest(model.decision_payload) != decisions[0][4]:
            raise FaultError("persisted model decision digest mismatch")
        clean_call = {key: call[key] for key in ("call_id", "name", "arguments")}
        base = {
            "execution_run_id": run_id,
            "activity_id": activity_id,
            "generation": state.retry_generation,
            "owner_user_id": owner,
            "token_version": principal.token_version,
            "approval_id": str(approval.approval_id),
            "model_activity_id": str(model.activity_id),
            "model_decision_digest": digest(model.decision_payload),
            "call_digest": digest(clean_call),
            "catalog_fingerprint": model.decision_payload["catalog"]["fingerprint"],
            "round": round_index,
            "ordinal": ordinal,
            "source_entity_id": state.source_entity_id,
        }
        return base, state, clean_call, principal, scope, authorization


async def prepare(shared, owner, run_id, activity_id, kind, version_id=None):
    from app.application.evaluation.recording_authority import validate_recording
    from app.domain.evaluation.recording import recording_key
    from app.domain.models.scope import Principal

    base, state, call, principal, scope, authorization = await pending(
        shared.uow_factory, owner, run_id, activity_id
    )
    async with shared.uow_factory(authorization) as work:
        if kind == "recorded_object_missing":
            binding = await work.evaluation_recording.binding(scope, UUID(run_id))
            if (
                binding is None
                or str(binding["version_id"]) != version_id
                or Principal.model_validate(binding["principal"]) != principal
            ):
                raise FaultError("recording binding changed")
            manifest = await validate_recording(work, scope, principal, UUID(version_id))
            admission = binding.get("admission") or {}
            config = await work.evaluation_configuration.get_version(
                scope, "config", UUID(admission["config_version_id"])
            )
            if (
                state.source_entity_type != "evaluation_recorded_case"
                or admission.get("source_entity_type") != state.source_entity_type
                or admission.get("source_entity_id") != state.source_entity_id
                or admission.get("purpose") != "evaluation_subject"
                or admission.get("policy_digest") != state.policy_snapshot.snapshot_digest
                or config["fingerprint"] != admission.get("config_fingerprint")
                or list(config["selection"]["tool_names"]) != admission.get("tool_names")
                or call["name"] not in admission["tool_names"]
            ):
                raise FaultError("recording admission changed")
            slots = [
                slot
                for slot in manifest.slots
                if (slot.tool, slot.branch, slot.parallel_group, slot.ordinal)
                == (call["name"], "root", f"round:{base['round']}", base["ordinal"])
            ]
            if len(slots) != 1:
                raise FaultError("recorded slot ambiguous")
            slot = slots[0]
            contract = next(
                c
                for c in manifest.contracts
                if c.name == slot.tool and c.digest == slot.contract_digest
            )
            if (
                not contract.policy.requires_approval()
                or recording_key(
                    slot.tool,
                    slot.contract_digest,
                    slot.rule.normalize(call["arguments"], contract.arguments_schema),
                    slot.branch,
                    slot.ordinal,
                    parallel_group=slot.parallel_group,
                    rule_version=slot.rule.version,
                )
                != slot.match_key
            ):
                raise FaultError("recorded arguments differ")
            obj = await work.evaluation_recording.object(scope, slot.object_id)
            body = await shared.object_storage.get_bytes(obj["storage_key"])
            import hashlib

            if (obj["digest"], obj["size_bytes"], hashlib.sha256(body).hexdigest(), len(body)) != (
                slot.result_digest,
                slot.result_bytes,
                slot.result_digest,
                slot.result_bytes,
            ):
                raise FaultError("positive real recorded object read failed")
            base.update(
                version_id=version_id,
                revision=manifest.revision,
                slot_id=str(slot.id),
                object_id=str(slot.object_id),
                storage_key=obj["storage_key"],
                object_digest=slot.result_digest,
                object_bytes=len(body),
                positive_read=True,
            )
        else:
            if state.source_entity_type != "session":
                raise FaultError("physical unknown requires normal session")
            session = await work.session.get_by_id(state.source_entity_id, scope=scope)
            marker = "a05-unknown-" + state.source_entity_id
            expected = {
                "session_id": marker,
                "exec_dir": "/home/ubuntu",
                "command": f"printf 'owned-write\\n' >> {marker}",
            }
            if session is None or not session.sandbox_id or call["arguments"] != expected:
                raise FaultError("owned physical write fixture mismatch")
            sandbox = await shared.sandbox_factory.get(session.sandbox_id)
            if sandbox is None or sandbox.id != session.sandbox_id:
                raise FaultError("owned sandbox unavailable before arm")
            try:
                absent = await sandbox.check_file_exists("/home/ubuntu/" + marker)
                if (
                    not absent.success
                    or not isinstance(absent.data, dict)
                    or absent.data.get("exists") is not False
                ):
                    raise FaultError("owned marker already exists or absence unverified")
            finally:
                await sandbox.client.aclose()
            base.update(
                marker_absent_before=True,
                sandbox_id=session.sandbox_id,
                arguments=expected,
                marker_path="/home/ubuntu/" + marker,
            )
    repeated, *_ = await pending(shared.uow_factory, owner, run_id, activity_id)
    if any(base[key] != value for key, value in repeated.items()):
        raise FaultError("approval/authority changed during positive read")
    return base


async def verify_started(factory, arm, request, context):
    from app.domain.execution.run import RunState
    from app.infrastructure.execution.models import ExecutionActivityTaskORM as Task
    from app.infrastructure.execution.models import ExecutionApprovalProjectionORM as Approval
    from app.infrastructure.execution.models import ExecutionRunProjectionORM as Run

    verify_request(arm, request, context)
    principal, _, authorization = await current(factory, arm["owner_user_id"])
    if principal.token_version != arm["token_version"]:
        raise FaultError("authority token changed")
    # MarkActivityCallStarted is accepted before the formal projector catches up.
    # The worker may enter this handler while its event is still unprojected.
    # Wait for the durable read model instead of mistaking that lag for a fault.
    for attempt in range(100):
        async with factory(authorization) as work:
            task = await work.db_session.get(Task, context.activity_id)
            row = await work.db_session.get(Run, context.run.run_id)
            approval = await work.db_session.get(Approval, UUID(arm["approval_id"]))
            if task is None or row is None or approval is None:
                raise FaultError("actual dispatch facts absent")
            state = RunState.model_validate(row.state)
            if (
                task.owner_user_id != arm["owner_user_id"]
                or task.team_id is not None
                or task.request_payload != request.input_payload
                or task.request_digest != request.input_digest
                or task.request_ref != request.input_ref
            ):
                raise FaultError("persisted request differs from actual worker request")
            claim = (context.activity_id, context.generation, context.claim_generation)
            if (
                task.status == "call_started"
                and task.call_started_at is not None
                and task.request_generation == context.generation
                and task.claim_generation == context.claim_generation
                and str(task.run_id) == arm["execution_run_id"]
                and claim in state.started_activity_claims
                and approval.status == "approved"
                and str(approval.subject_activity_id) == arm["activity_id"]
                and str(approval.run_id) == arm["execution_run_id"]
            ):
                return {
                    "claim_generation": task.claim_generation,
                    "request_digest": task.request_digest,
                    "request_event_position": task.request_event_position,
                    "approval_id": arm["approval_id"],
                }
        if attempt < 99:
            await asyncio.sleep(0.1)
    raise FaultError("actual approved call-started claim absent after projection wait")
