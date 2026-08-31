"""Approval-gated Ops Patrol remediation lifecycle."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
    PatrolTriggerType,
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

# ActuatorEnvelope.action == this domain enum value 1:1 (see
# ops-actuator/src/opencitadel_ops_actuator/server.py) — no mapping table
# needed. K8s `kind` casing differs though: the domain model stores the
# capitalized Kubernetes Kind ("Deployment"/"StatefulSet", matching probe
# args), while the Actuator's RestartRequest/ScaleRequest/RollbackRequest
# schemas declare kind as the lowercase Literal["deployment", "statefulset"].
_ACTUATOR_KIND_ALIASES: dict[str, str] = {
    "Deployment": "deployment",
    "StatefulSet": "statefulset",
}


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
            )
            remediation.idempotency_key = f"rem:{remediation.id}"

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
                },
                public_input={
                    "session_id": session.id,
                    "remediation_id": remediation.id,
                },
                idempotency_key=remediation.idempotency_key,
                parent_run_id=parent_run_id,
                correlation_id=parent_run_id,
                command_sink=uow.execution_commands,
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
    ) -> dict:
        """Execute an approved remediation through the registered Actuator.

        The Activity idempotency key authenticates the durable delivery but
        is never forwarded. The Actuator always receives the remediation key
        frozen when the proposal was admitted.
        """
        del idempotency_key

        if await self._remediation_mode() is not PatrolRemediationMode.ENABLED:
            raise BadRequestError(
                "Ops Patrol Remediation execution is disabled",
                error_key="apiErrors.patrolRemediation.executionDisabled",
            )

        async with self._uow_factory() as uow:
            # 1. Binding check: the tool call must belong to the exact session.
            # EXECUTING is deliberately resumable: the Actuator receives the
            # remediation's persisted idempotency key, so a worker crash after
            # this transition can safely repeat the same external operation.
            remediation = await uow.patrol.get_remediation(remediation_id, scope, for_update=True)
            if remediation is None:
                raise NotFoundError(
                    "Patrol Remediation 不存在", error_key="apiErrors.patrolRemediation.notFound"
                )
            if remediation.session_id != session_id:
                raise ConflictError(
                    "修复提案与当前会话不匹配或已被处理",
                    error_key="apiErrors.patrolRemediation.bindingMismatch",
                )
            already_executed = remediation.status == PatrolRemediationStatus.EXECUTED
            if not already_executed and remediation.status not in {
                PatrolRemediationStatus.PROPOSED,
                PatrolRemediationStatus.EXECUTING,
            }:
                raise ConflictError(
                    "修复提案与当前会话不匹配或已被处理",
                    error_key="apiErrors.patrolRemediation.bindingMismatch",
                )
            starting = remediation.status == PatrolRemediationStatus.PROPOSED

            # 2. params_hash re-verification: guards against the proposal row
            # being edited directly in the database after approval but before
            # execution (the approval was granted for a specific params_hash;
            # if the persisted params no longer hash to it, the approval no
            # longer covers what is about to run).
            recomputed_hash = patrol_remediation_params_hash(
                remediation.action.value,
                remediation.target_namespace,
                remediation.target_workload,
                remediation.target_kind,
                remediation.params,
            )
            if not already_executed and recomputed_hash != remediation.params_hash:
                remediation.status = PatrolRemediationStatus.FAILED
                remediation.error_code = "PARAMS_TAMPERED"
                remediation.error_message = (
                    "Persisted remediation no longer matches the approved proposal hash"
                )
                await uow.patrol.save_remediation(remediation)
                await uow.commit()
                self._governance_metrics.record_remediation_transition(remediation.status.value)
                await self._audit(
                    "patrol_remediation_params_tampered", remediation, scope.user_id, {}
                )
                raise ConflictError(
                    "修复提案参数已变更，拒绝执行",
                    error_key="apiErrors.patrolRemediation.paramsTampered",
                )

            if not already_executed and not remediation.target_workload.strip():
                raise BadRequestError(
                    "修复目标 workload 缺失，无法执行（propose 时未能从 Check 探测参数解析，也未提供覆盖值）",
                    error_key="apiErrors.patrolRemediation.workloadMissing",
                )

            # 3a. Capability baseline must already be persisted before the
            # remediation Activity is admitted and approved.
            # execute() only ever *reads* it here; a missing baseline means
            # the session was somehow never through that preflight (or ran
            # against a version of the code that predates it) and must fail
            # closed rather than silently trusting whatever the Actuator
            # reports right now.
            baseline_hash = remediation.actuator_capability_hash
            if not already_executed and not baseline_hash:
                remediation.status = PatrolRemediationStatus.FAILED
                remediation.error_code = "CAPABILITY_BASELINE_MISSING"
                remediation.error_message = (
                    "No capability baseline was persisted before this session was approved"
                )
                await uow.patrol.save_remediation(remediation)
                await uow.commit()
                self._governance_metrics.record_remediation_transition(remediation.status.value)
                await self._audit(
                    "patrol_remediation_capability_baseline_missing",
                    remediation,
                    scope.user_id,
                    {},
                )
                raise ConflictError(
                    "缺少执行前 capability 基线，拒绝执行",
                    error_key="apiErrors.patrolRemediation.capabilityBaselineMissing",
                )

            if not already_executed:
                server = await uow.mcp_server.get_by_name(ACTUATOR_MCP_SERVER_NAME)
                if server is None or not server.enabled:
                    raise NotFoundError(
                        "Ops Actuator 未注册或已禁用",
                        error_key="apiErrors.patrolRemediation.actuatorServerMissing",
                    )

            if starting:
                remediation.status = PatrolRemediationStatus.EXECUTING
                await uow.patrol.save_remediation(remediation)
                await uow.commit()
        if already_executed:
            remediation = await self._ensure_recheck(remediation, scope)
            return remediation.model_dump(mode="json")
        if starting:
            self._governance_metrics.record_remediation_transition(remediation.status.value)
            await self._audit(
                "patrol_remediation_executing",
                remediation,
                scope.user_id,
                {},
            )

        kind = _ACTUATOR_KIND_ALIASES.get(remediation.target_kind, remediation.target_kind.lower())
        action_args: dict = {
            "namespace": remediation.target_namespace,
            "workload": remediation.target_workload,
            "kind": kind,
            # Never the LLM/batch-executor value — always the record's own key.
            "idempotency_key": remediation.idempotency_key,
        }
        if remediation.action == PatrolRemediationAction.SCALE_WORKLOAD:
            action_args["replicas"] = remediation.params.get("replicas")

        try:
            # 3b. Live drift check: compare the Actuator's *current* capability
            # hash against the baseline persisted before approval (3a). This is
            # the check that actually spans the approval time window — the
            # baseline was captured at session-construction time (pre-approval),
            # this read happens post-approval, immediately before the write —
            # so a schema change made *during* human review is caught here.
            live = await self._actuator_client.get_capabilities(
                server,
                policy=policy,
            )
            live_hash = str(live.get("overall_capability_hash") or "")
            if not live_hash or live_hash != baseline_hash:
                async with self._uow_factory() as uow:
                    remediation.status = PatrolRemediationStatus.FAILED
                    remediation.error_code = "CAPABILITY_DRIFT"
                    remediation.error_message = f"Actuator capability hash drifted since baseline: baseline={baseline_hash} live={live_hash or '<empty>'}"
                    # actuator_capability_hash itself is left untouched — it
                    # stays the historical, pre-approval baseline for audit;
                    # the observed drifted value lives in error_message only.
                    await uow.patrol.save_remediation(remediation)
                    await uow.commit()
                self._governance_metrics.record_remediation_transition(remediation.status.value)
                await self._audit(
                    "patrol_remediation_capability_drift",
                    remediation,
                    scope.user_id,
                    {"baseline": baseline_hash, "live": live_hash},
                )
                raise ConflictError(
                    "Actuator capability 已漂移，拒绝执行",
                    error_key="apiErrors.patrolRemediation.capabilityDrift",
                )

            live_mode = await self._remediation_mode()
            if live_mode is not PatrolRemediationMode.ENABLED:
                remediation.status = PatrolRemediationStatus.FAILED
                remediation.error_code = "POLICY_DENIED"
                remediation.error_message = "Current Operations Policy denied remediation"
                async with self._uow_factory() as uow:
                    await uow.patrol.save_remediation(remediation)
                    await uow.commit()
                self._governance_metrics.record_remediation_transition(remediation.status.value)
                await self._audit(
                    "patrol_remediation_policy_denied",
                    remediation,
                    scope.user_id,
                    {"mode": live_mode.value},
                )
                raise BadRequestError(
                    "Ops Patrol Remediation execution is disabled",
                    error_key="apiErrors.patrolRemediation.executionDisabled",
                )

            envelope = await self._actuator_client.execute_action(
                server,
                remediation.action.value,
                action_args,
                policy=policy,
            )
        except (BadRequestError, ConflictError):
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            async with self._uow_factory() as uow:
                remediation.status = PatrolRemediationStatus.FAILED
                remediation.error_code = "ACTUATOR_UNREACHABLE"
                remediation.error_message = str(exc)[:2000]
                await uow.patrol.save_remediation(remediation)
                await uow.commit()
            self._governance_metrics.record_remediation_transition(remediation.status.value)
            await self._audit(
                "patrol_remediation_executed",
                remediation,
                scope.user_id,
                {"outcome": "actuator_unreachable"},
            )
            raise

        # 5. Record before/after + outcome; status -> EXECUTED / FAILED.
        # actuator_capability_hash is intentionally left as the pre-approval
        # baseline (live_hash == baseline_hash on this path anyway, since the
        # drift check above already required equality to reach here).
        remediation.before_observation = envelope.get("before")
        remediation.after_observation = envelope.get("after")
        outcome = envelope.get("action_outcome")
        if outcome == "failed":
            remediation.status = PatrolRemediationStatus.FAILED
            remediation.error_code = envelope.get("error_code") or "ACTUATOR_FAILED"
            remediation.error_message = envelope.get("error_message")
        else:
            remediation.status = PatrolRemediationStatus.EXECUTED
        async with self._uow_factory() as uow:
            await uow.patrol.save_remediation(remediation)
            await uow.commit()
        self._governance_metrics.record_remediation_transition(remediation.status.value)
        await self._audit(
            "patrol_remediation_executed",
            remediation,
            scope.user_id,
            {"outcome": outcome},
        )

        # 6. EXECUTED -> auto-dispatch a recheck Patrol run so finalize_run can
        # close the loop (resolve the Finding + mark the remediation VERIFIED,
        # or FAILED(recheck_failed) if the check still doesn't pass).
        remediation = await self._ensure_recheck(remediation, scope)

        return remediation.model_dump(mode="json")

    async def _ensure_recheck(
        self,
        remediation: PatrolRemediation,
        scope: OwnerScope,
    ) -> PatrolRemediation:
        if (
            remediation.status != PatrolRemediationStatus.EXECUTED
            or remediation.recheck_run_id is not None
        ):
            return remediation
        try:
            recheck_run = await self._patrol_run_service.trigger_pack(
                remediation.pack_id,
                scope,
                "system:remediation",
                idempotency_key=f"recheck:{remediation.id}",
                trigger_type=PatrolTriggerType.REMEDIATION,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "Failed to dispatch remediation recheck run remediation=%s: %s",
                remediation.id,
                exc,
            )
            await self._audit(
                "patrol_remediation_recheck_dispatch_failed",
                remediation,
                scope.user_id,
                {"error": str(exc)[:500]},
            )
            return remediation
        remediation.recheck_run_id = recheck_run.id
        async with self._uow_factory() as uow:
            await uow.patrol.save_remediation(remediation)
            await uow.commit()
        await self._audit(
            "patrol_remediation_recheck_started",
            remediation,
            scope.user_id,
            {"recheck_run_id": recheck_run.id},
        )
        return remediation

    async def cancel_if_pending(self, session_id: str) -> None:
        """Best-effort cleanup when a Remediation session ends without ever
        reaching a terminal state on its own.

        Two cases, both of which would otherwise leave a Finding permanently
        blocked (get_active_remediation_for_finding — and the DB partial
        unique index behind it — treat PROPOSED/EXECUTING/EXECUTED as
        in-flight, so a stuck row blocks any new proposal forever):

        1. PROPOSED -> CANCELLED: approval rejected, session cancelled/failed,
           or worker crash before the tool call ever ran.
        2. EXECUTING -> FAILED(session_terminated_mid_execution): the worker
           process died *during* execute() — after it flipped the row to
           EXECUTING (see execute() step 3a/EXECUTING transition) but before
           it could record an outcome. execute() only ever holds the
           for_update row lock for the PROPOSED->EXECUTING transition itself
           (it releases the lock before the actual Actuator network call), so
           this cannot fully serialize against a still-running execute() —
           but it reuses the same for_update-then-recheck-status pattern
           execute() uses, which is what prevents this method from clobbering
           a row that has *already* reached EXECUTED/FAILED by the time this
           runs (those statuses fall through to the no-op branch below).

        Mirrors PatrolRunService.mark_run_failed's role for
        `on_session_terminal`.
        """
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
                remediation.status = PatrolRemediationStatus.FAILED
                remediation.error_code = "session_terminated_mid_execution"
                remediation.error_message = (
                    "Session terminated while the Actuator call was in flight; "
                    "outcome unknown. Verify the workload's actual state before re-proposing."
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
