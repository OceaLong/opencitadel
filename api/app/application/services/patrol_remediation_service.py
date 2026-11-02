"""Approval-gated Ops Patrol remediation lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.application.execution.admission import RunAdmissionService
from app.application.ports.observability import GovernanceMetricsPort
from app.application.ports.remediation import (
    ACTUATOR_MCP_SERVER_NAME,
    PatrolActuatorPort,
)
from app.application.services.audit_service import AuditService
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.execution.run import RunFamily
from app.domain.models.audit_log import AuditLog
from app.domain.models.patrol import (
    PatrolFindingStatus,
    PatrolPackConfig,
    PatrolRemediation,
    PatrolRemediationAction,
    PatrolRemediationStatus,
    patrol_actuator_payload_digest,
    patrol_remediation_params_hash,
)
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionMode, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import ActivityExecutionPolicy, PatrolRemediationMode
from app.domain.utils.time_utils import utc_now

if TYPE_CHECKING:
    from app.application.services.patrol_run_service import PatrolRunService


logger = logging.getLogger(__name__)


# Closed-world catalog: only k8s_* probes have an actuator counterpart today.
# Any other probe family (http_probe, certificate_status, backup_status,
# dependency_status, prom_query) has an empty allowed-action set — propose()
# must reject every action for those Findings.
def _allowed_actions_for_probe_tool(tool: str) -> frozenset[PatrolRemediationAction]:
    if tool.startswith("k8s_"):
        return frozenset(PatrolRemediationAction)
    return frozenset()


# Per-action params whitelist. Keys outside this set are rejected outright;
# keys inside it are further validated below.
#
# ROLLBACK_WORKLOAD intentionally accepts no params: the Actuator's
# rollback_workload tool (ops-actuator/src/opencitadel_ops_actuator/server.py)
# has no `revision` argument at all — it always rolls back to the workload's
# immediately-previous ReplicaSet revision. Accepting a `revision` param here
# would make the approved proposal differ from the operation the Actuator can
# actually perform.
_PARAM_WHITELIST: dict[PatrolRemediationAction, frozenset[str]] = {
    PatrolRemediationAction.RESTART_WORKLOAD: frozenset(),
    PatrolRemediationAction.SCALE_WORKLOAD: frozenset({"replicas"}),
    PatrolRemediationAction.ROLLBACK_WORKLOAD: frozenset(),
}


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_params(action: PatrolRemediationAction, params: dict) -> None:
    allowed = _PARAM_WHITELIST[action]
    unknown = set(params) - allowed
    if unknown:
        raise BadRequestError(
            f"unsupported params for {action.value}: {sorted(unknown)}",
            error_key="apiErrors.patrolRemediation.paramsNotAllowed",
        )
    if action == PatrolRemediationAction.SCALE_WORKLOAD and not _is_positive_int(
        params.get("replicas")
    ):
        raise BadRequestError(
            "scale_workload requires a positive integer replicas",
            error_key="apiErrors.patrolRemediation.replicasInvalid",
        )


def _impact_summary(
    action: PatrolRemediationAction,
    namespace: str,
    workload: str,
    kind: str,
    params: dict,
) -> str:
    target = f"{kind}/{workload or '<unresolved>'} in {namespace}"
    if action == PatrolRemediationAction.RESTART_WORKLOAD:
        return (
            f"Restart {target}: causes a rolling pod recreation, brief availability dip expected."
        )
    if action == PatrolRemediationAction.SCALE_WORKLOAD:
        return f"Scale {target} to {params.get('replicas')} replicas."
    # rollback_workload has no `revision` param (see _PARAM_WHITELIST) — the
    # Actuator always rolls back to the immediately-previous revision, so the
    # approval-facing summary must not imply a caller-chosen target version.
    return f"Roll {target} back to the previous revision."


def _rollback_hint(action: PatrolRemediationAction) -> str:
    if action == PatrolRemediationAction.RESTART_WORKLOAD:
        return "Restart is non-destructive; no rollback action required."
    if action == PatrolRemediationAction.SCALE_WORKLOAD:
        return "Scale the workload back to its prior replica count if this causes regressions."
    return "Roll forward to the previous revision recorded before this action."


def _require_effect_capabilities(manifest: dict, action: str, target_ref: str | None) -> None:
    effect = manifest.get("effect_contract") or {}
    if (
        manifest.get("schema_version") != 2
        or manifest.get("target_ref") != target_ref
        or effect.get("version") != 2
        or effect.get("execute") is not True
        or effect.get("resolve") != "get_operation"
        or effect.get("target_identity") != "get_target"
        or not {action, "get_operation", "get_target"} <= set(manifest.get("enabled_tools", []))
    ):
        raise ConflictError("Actuator lacks the approved durable operation contract")


def _valid_operation_identity(remediation: PatrolRemediation) -> bool:
    return bool(
        remediation.actuator_operation_id == remediation.id
        and remediation.actuator_target_uid
        and remediation.actuator_target_ref
        and remediation.idempotency_key == f"rem:{remediation.id}"
        and remediation.actuator_payload_digest == patrol_actuator_payload_digest(remediation)
    )


def _verified_operation_receipt(
    remediation: PatrolRemediation, envelope: dict
) -> tuple[str, dict | None]:
    """Only a bound, retained receipt proves applied or not_applied, never an outcome label."""
    if not isinstance(envelope, dict):
        raise TypeError("provider envelope must be an object")
    state = envelope.get("state")
    if state not in {"applied", "not_applied"}:
        return "unknown", None
    receipt = envelope.get("receipt")
    if not isinstance(receipt, dict):
        raise TypeError("durable receipt required")
    key_digest = hashlib.sha256(remediation.idempotency_key.encode()).hexdigest()
    target = {
        "cluster": remediation.actuator_target_ref,
        "namespace": remediation.target_namespace,
        "workload": remediation.target_workload,
        "kind": remediation.target_kind.lower(),
        "uid": remediation.actuator_target_uid,
    }
    expected = {
        "operation_id": remediation.actuator_operation_id,
        "payload_digest": remediation.actuator_payload_digest,
        "key_digest": key_digest,
        "target": target,
        "action": remediation.action.value,
        "state": state,
    }
    if (
        envelope.get("schema_version") != 2
        or envelope.get("target_ref") != remediation.actuator_target_ref
    ):
        raise ValueError("wrong provider envelope")
    if any(
        envelope.get(key) != value or receipt.get(key) != value for key, value in expected.items()
    ):
        raise ValueError("receipt binding mismatch")
    revision = receipt.get("revision")
    if (
        receipt.get("schema_version") != 1
        or type(revision) is not int
        or revision < 1
        or envelope.get("receipt_revision") != revision
        or not receipt.get("caller_authority")
        or not receipt.get("provider_namespace")
    ):
        raise ValueError("receipt identity missing")
    canonical = json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    expected_hash = hashlib.sha256(canonical.encode()).hexdigest()
    evidence_ref = f"actuator://operations/{remediation.actuator_operation_id}/receipts/{revision}"
    evidence = next(
        (
            item
            for item in envelope.get("evidence", [])
            if isinstance(item, dict) and item.get("ref") == evidence_ref
        ),
        None,
    )
    if (
        not evidence
        or evidence.get("sha256") != expected_hash
        or evidence.get("target_ref") != remediation.actuator_target_ref
    ):
        raise ValueError("receipt evidence unavailable")
    expires = datetime.fromisoformat(evidence["expires_at"])
    if expires.tzinfo is None or expires <= utc_now():
        raise ValueError("receipt evidence expired")
    return state, receipt


class PatrolRemediationService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        actuator_client: PatrolActuatorPort,
        patrol_run_service: PatrolRunService,
        run_admission_service: RunAdmissionService,
        policy_reader: OperationsPolicyReader,
        governance_metrics: GovernanceMetricsPort,
        audit_service: AuditService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._actuator_client = actuator_client
        self._patrol_run_service = patrol_run_service
        self._run_admission = run_admission_service
        self._policy_reader = policy_reader
        self._governance_metrics = governance_metrics

    async def _remediation_mode(self) -> PatrolRemediationMode:
        active = await self._policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        return active.revision.policy.patrol.remediation

    async def _audit(
        self,
        action: str,
        remediation: PatrolRemediation,
        actor_user_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        if self._audit_service is None:
            return
        await self._audit_service.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                resource_type="patrol_remediation",
                resource_id=remediation.id,
                session_id=remediation.session_id,
                metadata={
                    "finding_id": remediation.finding_id,
                    "run_id": remediation.run_id,
                    "session_id": remediation.session_id,
                    "status": remediation.status.value,
                    **(metadata or {}),
                },
            )
        )

    async def propose(
        self,
        finding_id: str,
        action: PatrolRemediationAction,
        params: dict,
        scope: OwnerScope,
        actor_user_id: str,
        *,
        workload: str | None = None,
    ) -> PatrolRemediation:
        if workload is not None and not workload.strip():
            raise BadRequestError(
                "workload override must not be blank",
                error_key="apiErrors.patrolRemediation.workloadInvalid",
            )

        async with self._uow_factory() as uow:
            finding = await uow.patrol.get_finding(finding_id, scope)
            if finding is None:
                raise NotFoundError(
                    "Patrol Finding 不存在",
                    error_key="apiErrors.patrolRemediation.findingNotFound",
                )
            if finding.status not in {
                PatrolFindingStatus.OPEN,
                PatrolFindingStatus.ACKNOWLEDGED,
            }:
                raise ConflictError(
                    "仅 open/acknowledged Finding 可发起修复",
                    error_key="apiErrors.patrolRemediation.findingNotActionable",
                )

            run = await uow.patrol.get_run(finding.run_id, scope)
            if run is None:
                raise NotFoundError(
                    "Patrol Run 不存在", error_key="apiErrors.patrolRemediation.runNotFound"
                )

            check_results = await uow.patrol.list_check_results(run.id, scope)
            check_result = next(
                (item for item in check_results if item.id == finding.check_result_id),
                None,
            )
            if check_result is None:
                raise NotFoundError(
                    "Patrol Check Result 不存在",
                    error_key="apiErrors.patrolRemediation.checkResultNotFound",
                )

            config = PatrolPackConfig.model_validate(run.pack_snapshot["config"])
            check = next(
                (item for item in config.checks if item.id == check_result.check_id),
                None,
            )
            if check is None:
                raise NotFoundError(
                    "Patrol Check 不存在", error_key="apiErrors.patrolRemediation.checkNotFound"
                )

            allowed_actions = _allowed_actions_for_probe_tool(check.probe.tool)
            if action not in allowed_actions:
                raise BadRequestError(
                    f"probe '{check.probe.tool}' does not support remediation action '{action.value}'",
                    error_key="apiErrors.patrolRemediation.actionNotAllowed",
                )
            _validate_params(action, params)

            if await uow.patrol.get_active_remediation_for_finding(finding.id) is not None:
                raise ConflictError(
                    "Finding 已有进行中的修复提案",
                    error_key="apiErrors.patrolRemediation.activeRemediationExists",
                )

            namespace = str(check.probe.args.get("namespace") or "")
            if not namespace:
                raise BadRequestError(
                    "Check probe 缺少 namespace，无法定位修复目标",
                    error_key="apiErrors.patrolRemediation.namespaceMissing",
                )
            resolved_workload = (
                workload.strip()
                if workload is not None
                else str(check.probe.args.get("workload") or "")
            )
            kind = str(check.probe.args.get("kind") or "Deployment")
            if not resolved_workload:
                raise BadRequestError(
                    "修复提案需要具体 workload，不能审批未解析的目标",
                    error_key="apiErrors.patrolRemediation.workloadMissing",
                )

            params_hash = patrol_remediation_params_hash(
                action.value, namespace, resolved_workload, kind, params
            )

            # Capture the Actuator capability baseline BEFORE the remediation is
            # admitted and sent for approval. execute() re-reads the live hash
            # post-approval and refuses to act if it drifted. Without this
            # preflight the baseline stayed None and every approved remediation
            # failed closed with CAPABILITY_BASELINE_MISSING.
            actuator_server = await uow.mcp_server.get_by_name(ACTUATOR_MCP_SERVER_NAME)
            if actuator_server is None or not actuator_server.enabled:
                raise NotFoundError(
                    "Ops Actuator 未注册或已禁用",
                    error_key="apiErrors.patrolRemediation.actuatorServerMissing",
                )
            baseline = await self._actuator_client.get_capabilities(
                actuator_server,
                policy=ActivityExecutionPolicy(),
            )
            baseline_hash = str(baseline.get("overall_capability_hash") or "")
            if not baseline_hash:
                raise ConflictError(
                    "无法获取 Ops Actuator capability 基线，拒绝创建修复提案",
                    error_key="apiErrors.patrolRemediation.capabilityBaselineUnavailable",
                )
            _require_effect_capabilities(baseline, action.value, config.target_ref)
            target = await self._actuator_client.get_target(
                actuator_server,
                namespace=namespace,
                workload=resolved_workload,
                kind=kind.lower(),
                policy=ActivityExecutionPolicy(),
            )
            if (
                target.get("schema_version") != 2
                or target.get("target_ref") != config.target_ref
                or target.get("namespace") != namespace
                or target.get("workload") != resolved_workload
                or target.get("kind") != kind.lower()
                or not isinstance(target.get("uid"), str)
                or not re.fullmatch(r"[A-Za-z0-9:_-]{1,200}", target["uid"])
            ):
                raise ConflictError(
                    "Actuator target identity unavailable or does not match proposal"
                )

            remediation = PatrolRemediation(
                pack_id=run.pack_id,
                run_id=run.id,
                finding_id=finding.id,
                check_result_id=check_result.id,
                fingerprint=finding.fingerprint,
                action=action,
                target_namespace=namespace,
                target_workload=resolved_workload,
                target_kind=kind,
                params=params,
                params_hash=params_hash,
                impact_summary=_impact_summary(action, namespace, resolved_workload, kind, params),
                rollback_hint=_rollback_hint(action),
                idempotency_key="pending",
                created_by=actor_user_id,
                actuator_capability_hash=baseline_hash,
                actuator_target_uid=target["uid"],
                actuator_target_ref=target["target_ref"],
            )
            remediation.idempotency_key = f"rem:{remediation.id}"
            remediation.actuator_operation_id = remediation.id
            remediation.actuator_payload_digest = patrol_actuator_payload_digest(remediation)

            # The session is the user-facing owner of the formal remediation
            # Run and its persisted approval request.
            session = Session(
                title=f"[修复提案] {finding.title}",
                owner_user_id=scope.user_id,
                team_id=scope.team_id,
                mode=SessionMode.AGENT,
                status=SessionStatus.PENDING,
            )
            remediation.session_id = session.id
            session.status = SessionStatus.RUNNING
            if await self._remediation_mode() is PatrolRemediationMode.DISABLED:
                await self._audit(
                    "patrol_remediation_policy_denied",
                    remediation,
                    actor_user_id,
                    {"mode": PatrolRemediationMode.DISABLED.value},
                )
                raise BadRequestError(
                    "Ops Patrol Remediation is disabled",
                    error_key="apiErrors.patrolRemediation.disabled",
                )
            await uow.session.save(session)
            await uow.patrol.save_remediation(remediation)
            self._governance_metrics.record_remediation_transition(remediation.status.value)
            if run.execution_run_id is None:
                raise ConflictError(
                    "Patrol Run has no formal execution parent",
                    error_key="apiErrors.patrolRemediation.executionParentMissing",
                )
            parent_run_id = run.execution_run_id
            await self._run_admission.admit(
                family=RunFamily.REMEDIATION,
                source_entity_type="session",
                source_entity_id=session.id,
                owner_scope=scope,
                private_input={
                    "session_id": session.id,
                    "remediation_id": remediation.id,
                    "action": action.value,
                    "params": params,
                    "operation_id": remediation.actuator_operation_id,
                    "payload_digest": remediation.actuator_payload_digest,
                    "target_uid": remediation.actuator_target_uid,
                    "target_ref": remediation.actuator_target_ref,
                },
                public_input={
                    "session_id": session.id,
                    "remediation_id": remediation.id,
                },
                idempotency_key=remediation.idempotency_key,
                parent_run_id=parent_run_id,
                correlation_id=parent_run_id,
                command_sink=uow.execution_commands,
                inference_read_context=uow,
            )
            await uow.commit()

        await self._audit(
            "patrol_remediation_proposed",
            remediation,
            actor_user_id,
            {
                "action": action.value,
                "params_hash": params_hash,
                "target": f"{kind}/{namespace}/{resolved_workload or '<unresolved>'}",
            },
        )

        return remediation

    async def execute(
        self,
        remediation_id: str,
        session_id: str,
        idempotency_key: str,
        scope: OwnerScope,
        *,
        policy: ActivityExecutionPolicy,
        persist_outcome: bool = True,
    ) -> dict:
        """Send a newly approved operation once; an interrupted call is query-only."""
        del idempotency_key  # Delivery identity never replaces the approved rem:{id} key.
        remediation = await self.get(remediation_id, scope)
        if remediation.session_id != session_id:
            raise ConflictError(
                "修复提案与当前会话不匹配", error_key="apiErrors.patrolRemediation.bindingMismatch"
            )
        if remediation.status in {
            PatrolRemediationStatus.EXECUTING,
            PatrolRemediationStatus.UNKNOWN,
        }:
            return await self.resolve_operation(
                remediation_id, scope, policy=policy, persist_outcome=persist_outcome
            )
        if remediation.status == PatrolRemediationStatus.EXECUTED:
            return (await self._ensure_recheck(remediation, scope)).model_dump(mode="json")
        if remediation.status != PatrolRemediationStatus.PROPOSED:
            raise ConflictError(
                "修复提案已处理", error_key="apiErrors.patrolRemediation.bindingMismatch"
            )
        if await self._remediation_mode() is not PatrolRemediationMode.ENABLED:
            raise BadRequestError(
                "Ops Patrol Remediation execution is disabled",
                error_key="apiErrors.patrolRemediation.executionDisabled",
            )

        recomputed = patrol_remediation_params_hash(
            remediation.action.value,
            remediation.target_namespace,
            remediation.target_workload,
            remediation.target_kind,
            remediation.params,
        )
        if recomputed != remediation.params_hash:
            await self._reject_unsent(remediation, scope, "PARAMS_TAMPERED")
            raise ConflictError(
                "修复提案参数已变更，拒绝执行",
                error_key="apiErrors.patrolRemediation.paramsTampered",
            )
        if not remediation.actuator_capability_hash:
            await self._reject_unsent(remediation, scope, "CAPABILITY_BASELINE_MISSING")
            raise ConflictError(
                "缺少执行前 capability 基线",
                error_key="apiErrors.patrolRemediation.capabilityBaselineMissing",
            )
        if not _valid_operation_identity(remediation):
            await self._reject_unsent(remediation, scope, "OPERATION_IDENTITY_MISSING")
            raise ConflictError("修复提案缺少已批准的 operation 身份")
        async with self._uow_factory() as uow:
            server = await uow.mcp_server.get_by_name(ACTUATOR_MCP_SERVER_NAME)
        if server is None or not server.enabled:
            raise NotFoundError(
                "Ops Actuator 未注册或已禁用",
                error_key="apiErrors.patrolRemediation.actuatorServerMissing",
            )
        live = await self._actuator_client.get_capabilities(server, policy=policy)
        if live.get("overall_capability_hash") != remediation.actuator_capability_hash:
            await self._reject_unsent(remediation, scope, "CAPABILITY_DRIFT")
            raise ConflictError(
                "Actuator capability 已漂移，拒绝执行",
                error_key="apiErrors.patrolRemediation.capabilityDrift",
            )
        _require_effect_capabilities(
            live, remediation.action.value, remediation.actuator_target_ref
        )
        if await self._remediation_mode() is not PatrolRemediationMode.ENABLED:
            await self._reject_unsent(remediation, scope, "POLICY_DENIED")
            raise BadRequestError(
                "Ops Patrol Remediation execution is disabled",
                error_key="apiErrors.patrolRemediation.executionDisabled",
            )

        # Claim the only send after preflight. Concurrent callers and crash recovery
        # query this operation; they cannot acquire a second send authorization.
        async with self._uow_factory() as uow:
            current = await uow.patrol.get_remediation(remediation_id, scope, for_update=True)
            if current is None or current.session_id != session_id:
                raise NotFoundError("Remediation not found")
            starting = current.status == PatrolRemediationStatus.PROPOSED
            if starting:
                if (
                    current.params_hash,
                    current.actuator_payload_digest,
                    current.actuator_capability_hash,
                ) != (
                    remediation.params_hash,
                    remediation.actuator_payload_digest,
                    remediation.actuator_capability_hash,
                ) or not _valid_operation_identity(current):
                    raise ConflictError("Remediation changed during preflight")
                current.status = PatrolRemediationStatus.EXECUTING
                await uow.patrol.save_remediation(current)
                await uow.commit()
            remediation = current
        if not starting:
            return await self.resolve_operation(
                remediation_id, scope, policy=policy, persist_outcome=persist_outcome
            )
        self._governance_metrics.record_remediation_transition("executing")
        await self._audit("patrol_remediation_executing", remediation, scope.user_id)
        arguments = {
            "namespace": remediation.target_namespace,
            "workload": remediation.target_workload,
            "kind": remediation.target_kind.lower(),
            "idempotency_key": remediation.idempotency_key,
            "operation_id": remediation.actuator_operation_id,
            "payload_digest": remediation.actuator_payload_digest,
            "target_uid": remediation.actuator_target_uid,
            **remediation.params,
        }
        try:
            envelope = await self._actuator_client.execute_action(
                server,
                remediation.action.value,
                arguments,
                policy=policy,
            )
        except Exception:  # noqa: BLE001 - every post-send failure leaves the effect unresolved.
            return await self._record_operation_result(
                remediation, scope, None, "ACTUATOR_OUTCOME_UNKNOWN", persist=persist_outcome
            )
        return await self._record_operation_result(
            remediation, scope, envelope, persist=persist_outcome
        )

    async def resolve_operation(
        self,
        remediation_id: str,
        scope: OwnerScope,
        *,
        policy: ActivityExecutionPolicy,
        persist_outcome: bool = True,
    ) -> dict:
        """Trusted provider query used by recovery; never calls a write tool.

        Returns the original operation identity and verified provider receipt for
        the formal EffectStore adapter. It does not settle physical usage holds.
        """
        remediation = await self.get(remediation_id, scope)
        if remediation.error_code == "ACTUATOR_EVIDENCE_CONFLICT" or remediation.status not in {
            PatrolRemediationStatus.EXECUTING,
            PatrolRemediationStatus.UNKNOWN,
        }:
            return remediation.model_dump(mode="json")
        if not _valid_operation_identity(remediation):
            return await self._record_operation_result(
                remediation, scope, None, "OPERATION_IDENTITY_MISSING", persist=persist_outcome
            )
        async with self._uow_factory() as uow:
            server = await uow.mcp_server.get_by_name(ACTUATOR_MCP_SERVER_NAME)
        try:
            if server is None or not server.enabled:
                raise ValueError("Actuator unavailable")
            live = await self._actuator_client.get_capabilities(server, policy=policy)
            _require_effect_capabilities(
                live, remediation.action.value, remediation.actuator_target_ref
            )
            if live.get("overall_capability_hash") != remediation.actuator_capability_hash:
                raise ValueError("Actuator contract changed")
            envelope = await self._actuator_client.get_operation(
                server,
                remediation.actuator_operation_id,
                policy=policy,
            )
        except Exception:  # noqa: BLE001 - resolution failure never proves not_applied.
            return await self._record_operation_result(
                remediation, scope, None, "ACTUATOR_RESOLVE_UNAVAILABLE", persist=persist_outcome
            )
        return await self._record_operation_result(
            remediation, scope, envelope, persist=persist_outcome
        )

    async def _reject_unsent(self, remediation, scope, code):
        async with self._uow_factory() as uow:
            current = await uow.patrol.get_remediation(remediation.id, scope, for_update=True)
            if current is None or current.status != PatrolRemediationStatus.PROPOSED:
                return
            current.status = PatrolRemediationStatus.FAILED
            current.error_code = code
            current.error_message = code
            await uow.patrol.save_remediation(current)
            await uow.commit()
        self._governance_metrics.record_remediation_transition("failed")
        await self._audit(
            "patrol_remediation_rejected_before_send", current, scope.user_id, {"reason": code}
        )

    async def _record_operation_result(
        self, remediation, scope, envelope, error_code=None, *, persist=True
    ):
        receipt = None
        state = "unknown"
        try:
            if envelope is not None:
                state, receipt = _verified_operation_receipt(remediation, envelope)
        except (TypeError, ValueError, KeyError):
            error_code = "ACTUATOR_RECEIPT_INVALID"
        # Applied without result material remains explicitly unresolved for the
        # Activity; root's result-supply protocol may later complete it once.
        missing_result = state == "applied" and not isinstance(receipt.get("after"), dict)
        if missing_result:
            error_code = "ACTUATOR_RESULT_MATERIAL_MISSING"
        if not persist:
            observed = remediation.model_copy(deep=True)
            observed.actuator_receipt = receipt
            observed.before_observation = receipt.get("before") if receipt else None
            observed.after_observation = receipt.get("after") if receipt else None
            observed.status = (
                PatrolRemediationStatus.EXECUTED
                if state == "applied" and not missing_result
                else PatrolRemediationStatus.FAILED
                if state == "not_applied"
                else PatrolRemediationStatus.UNKNOWN
            )
            observed.error_code = error_code
            return observed.model_dump(mode="json")
        async with self._uow_factory() as uow:
            current = await uow.patrol.get_remediation(remediation.id, scope, for_update=True)
            if current is None:
                raise NotFoundError("Remediation not found")
            if current.error_code == "ACTUATOR_EVIDENCE_CONFLICT" or current.status not in {
                PatrolRemediationStatus.EXECUTING,
                PatrolRemediationStatus.UNKNOWN,
            }:
                return current.model_dump(mode="json")
            if receipt is not None:
                current.actuator_receipt = receipt
                current.before_observation = receipt.get("before")
                current.after_observation = receipt.get("after")
            if state == "applied" and not missing_result:
                current.status = PatrolRemediationStatus.EXECUTED
                current.error_code = current.error_message = None
            elif state == "not_applied":
                current.status = PatrolRemediationStatus.FAILED
                current.error_code = receipt.get("error_code") or "ACTUATOR_NOT_APPLIED"
                current.error_message = current.error_code
            else:
                current.status = PatrolRemediationStatus.UNKNOWN
                current.error_code = error_code or "ACTUATOR_OUTCOME_UNKNOWN"
                current.error_message = "Original provider operation remains unresolved; query its receipt before any new action."
            await uow.patrol.save_remediation(current)
            await uow.commit()
        self._governance_metrics.record_remediation_transition(current.status.value)
        await self._audit(
            "patrol_remediation_operation_observed",
            current,
            scope.user_id,
            {"operation_id": current.actuator_operation_id, "provider_state": state},
        )
        if current.status == PatrolRemediationStatus.EXECUTED:
            current = await self._ensure_recheck(current, scope)
        return current.model_dump(mode="json")

    async def apply_effect_fact(self, remediation_id, scope, *, state, result, receipt):
        """Project a committed Run effect fact. This cannot create operation authority."""
        async with self._uow_factory() as uow:
            current = await uow.patrol.get_remediation(remediation_id, scope, for_update=True)
            if current is None or current.status == PatrolRemediationStatus.CANCELLED:
                return
            quarantined = (
                state == "integrity_conflict" or current.error_code == "ACTUATOR_EVIDENCE_CONFLICT"
            )
            if current.status == PatrolRemediationStatus.VERIFIED and not quarantined:
                return
            if quarantined:
                current.status = PatrolRemediationStatus.UNKNOWN
                current.error_code = "ACTUATOR_EVIDENCE_CONFLICT"
                current.error_message = "Contradictory external receipts require evidence review"
            elif state == "applied" and result is not None:
                current.status = PatrolRemediationStatus.EXECUTED
                current.after_observation = result.get("after_observation")
                current.error_code = current.error_message = None
            elif state == "not_applied":
                current.status = PatrolRemediationStatus.FAILED
                current.error_code = "ACTUATOR_NOT_APPLIED"
                current.error_message = "External operation verified not applied"
            else:
                current.status = PatrolRemediationStatus.UNKNOWN
                current.error_code = (
                    "ACTUATOR_RESULT_MATERIAL_MISSING"
                    if state == "applied"
                    else "ACTUATOR_OUTCOME_UNKNOWN"
                )
            if receipt is not None and not quarantined:
                current.actuator_receipt = receipt
            await uow.patrol.save_remediation(current)
            await uow.commit()
        if current.status == PatrolRemediationStatus.EXECUTED:
            await self._ensure_recheck(current, scope)

    async def retry_recheck(self, remediation_id: str, scope: OwnerScope) -> PatrolRemediation:
        remediation = await self.get(remediation_id, scope)
        if remediation.status != PatrolRemediationStatus.EXECUTED:
            raise ConflictError("Only an executed remediation awaiting verification can be retried")
        result = await self._ensure_recheck(remediation, scope, force=True)
        await self._audit("patrol_remediation_recheck_retry", result, scope.user_id, {})
        return result

    async def reconcile_rechecks(self, *, limit: int = 100) -> None:
        """Recover committed actuator success even after its activity/session has ended."""
        async with self._uow_factory() as uow:
            pending = await uow.patrol.list_pending_rechecks(utc_now(), limit=limit)
        for remediation in pending:
            try:
                async with self._uow_factory() as uow:
                    original = await uow.patrol.get_run(remediation.run_id)
                if original is None:
                    continue
                owner = original.pack_snapshot.get("owner_user_id") or remediation.created_by
                team = original.pack_snapshot.get("team_id")
                scope = OwnerScope.team(owner, team) if team else OwnerScope.personal(owner)
                if remediation.recheck_run_id:
                    await self._patrol_run_service.reconcile_recheck(remediation.id, scope)
                else:
                    await self._ensure_recheck(remediation, scope)
            except Exception:
                logger.exception("Failed to reconcile remediation %s", remediation.id)

    async def _ensure_recheck(
        self,
        remediation: PatrolRemediation,
        scope: OwnerScope,
        *,
        force: bool = False,
    ) -> PatrolRemediation:
        # Reserve a bounded retry before dispatch. A crash here becomes eligible
        # again after backoff; no retry path can invoke the actuator.
        async with self._uow_factory() as uow:
            current = await uow.patrol.get_remediation(remediation.id, scope, for_update=True)
            if current is None:
                raise NotFoundError("Remediation not found")
            if current.status != PatrolRemediationStatus.EXECUTED or current.recheck_run_id:
                return current
            now = utc_now()
            if not force and (
                current.recheck_dispatch_attempts >= 5
                or (current.recheck_next_attempt_at and current.recheck_next_attempt_at > now)
            ):
                return current
            if force:
                current.recheck_dispatch_attempts = 0
            current.recheck_dispatch_attempts += 1
            current.recheck_next_attempt_at = now + timedelta(
                seconds=30 * 2 ** (current.recheck_dispatch_attempts - 1)
            )
            current.updated_at = now
            await uow.patrol.save_remediation(current)
            await uow.commit()
        try:
            await self._patrol_run_service.trigger_recheck(current.id, scope)
        except Exception as exc:
            # The child, link and command have rolled back together. Re-read
            # under lock so a concurrent successful retry is never overwritten.
            async with self._uow_factory() as uow:
                current = await uow.patrol.get_remediation(current.id, scope, for_update=True)
                if current is None:
                    raise NotFoundError("Remediation not found") from exc
                if (
                    current.status == PatrolRemediationStatus.EXECUTED
                    and not current.recheck_run_id
                ):
                    current.error_code = "recheck_dispatch_failed"
                    current.error_message = str(exc)[:2000]
                    current.updated_at = utc_now()
                    await uow.patrol.save_remediation(current)
                    await uow.commit()
            await self._audit(
                "patrol_remediation_recheck_dispatch_failed",
                current,
                scope.user_id,
                {"error": str(exc)[:500], "attempt": current.recheck_dispatch_attempts},
            )
            return current
        current = await self.get(current.id, scope)
        await self._audit(
            "patrol_remediation_recheck_started",
            current,
            scope.user_id,
            {"recheck_run_id": current.recheck_run_id},
        )
        return current

    async def cancel_if_pending(self, session_id: str) -> None:
        """Cancellation stops waiting; it never proves an in-flight effect failed."""
        async with self._uow_factory() as uow:
            located = await uow.patrol.get_remediation_by_session_id(session_id)
            if located is None:
                return
            remediation = await uow.patrol.get_remediation(located.id, None, for_update=True)
            if remediation is None:
                return
            if remediation.status == PatrolRemediationStatus.PROPOSED:
                remediation.status = PatrolRemediationStatus.CANCELLED
                remediation.error_code = "SESSION_TERMINATED"
            elif remediation.status == PatrolRemediationStatus.EXECUTING:
                remediation.status = PatrolRemediationStatus.UNKNOWN
                remediation.error_code = "session_terminated_mid_execution"
                remediation.error_message = (
                    "Session terminated while the Actuator call was in flight; "
                    "outcome unknown. Resolve the original provider operation before any new action."
                )
            else:
                return
            await uow.patrol.save_remediation(remediation)
            await uow.commit()
        self._governance_metrics.record_remediation_transition(remediation.status.value)
        action = (
            "patrol_remediation_cancelled"
            if remediation.status == PatrolRemediationStatus.CANCELLED
            else "patrol_remediation_execution_aborted"
        )
        await self._audit(action, remediation, None, {"error_code": remediation.error_code})

    async def get(self, remediation_id: str, scope: OwnerScope) -> PatrolRemediation:
        async with self._uow_factory() as uow:
            remediation = await uow.patrol.get_remediation(remediation_id, scope)
        if remediation is None:
            raise NotFoundError(
                "Patrol Remediation 不存在", error_key="apiErrors.patrolRemediation.notFound"
            )
        return remediation

    async def list_for_run(self, run_id: str, scope: OwnerScope) -> list[PatrolRemediation]:
        async with self._uow_factory() as uow:
            return await uow.patrol.list_remediations_for_run(run_id, scope)
