"""Patrol Run orchestration and authoritative result finalization."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import NAMESPACE_URL, uuid5

from app.application.execution.admission import (
    RunAdmissionService,
    run_id_for_idempotency_key,
)
from app.application.execution.command_ingress import CommandIngress
from app.application.ports.observability import GovernanceMetricsPort
from app.application.services.artifact_service import ArtifactService
from app.application.services.audit_service import AuditService
from app.application.services.notification_service import NotificationService
from app.application.services.patrol_report_service import PatrolReportService
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.domain.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.domain.execution.commands import CommandContext, RegisteredCommand
from app.domain.execution.run import RunFamily
from app.domain.models.audit_log import AuditLog
from app.domain.models.patrol import (
    PatrolCheckResult,
    PatrolCheckStatus,
    PatrolFinding,
    PatrolFindingStatus,
    PatrolObservationSubmission,
    PatrolPackConfig,
    PatrolPackStatus,
    PatrolRemediation,
    PatrolRemediationStatus,
    PatrolRun,
    PatrolRunStatus,
    PatrolTriggerType,
    patrol_fingerprint,
)
from app.domain.models.scheduled_job import ScheduledJob, ScheduledRunStatus
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session, SessionMode, SessionStatus
from app.domain.repositories.uow import IUnitOfWork
from app.domain.runtime_policy import PatrolAdmissionMode
from app.domain.services.patrol_assertion_engine import PatrolAssertionEngine
from app.domain.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


class PatrolRunService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        run_admission_service: RunAdmissionService,
        command_ingress: CommandIngress,
        policy_reader: OperationsPolicyReader,
        fixture_replay_enabled: bool,
        governance_metrics: GovernanceMetricsPort,
        audit_service: AuditService | None = None,
        artifact_service: ArtifactService | None = None,
        notification_service: NotificationService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._artifact_service = artifact_service
        self._notification_service = notification_service
        self._run_admission = run_admission_service
        self._commands = command_ingress
        self._policy_reader = policy_reader
        self._fixture_replay_enabled = fixture_replay_enabled
        self._governance_metrics = governance_metrics

    async def _audit(
        self,
        action: str,
        run: PatrolRun,
        actor_user_id: str | None,
        metadata: dict | None = None,
    ) -> None:
        if self._audit_service is None:
            return
        await self._audit_service.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                resource_type="patrol_run",
                resource_id=run.id,
                team_id=run.pack_snapshot.get("team_id"),
                session_id=run.session_id,
                metadata={
                    "pack_id": run.pack_id,
                    "session_id": run.session_id,
                    "status": run.status.value,
                    **(metadata or {}),
                },
            )
        )

    async def trigger_pack(
        self,
        pack_id: str,
        scope: OwnerScope,
        actor_user_id: str,
        *,
        idempotency_key: str,
        trigger_type: PatrolTriggerType = PatrolTriggerType.MANUAL,
        automation_job: ScheduledJob | None = None,
        automation_firing_id: str | None = None,
        automation_fired_at: datetime | None = None,
    ) -> PatrolRun:
        if not idempotency_key.strip():
            raise BadRequestError(
                "Idempotency-Key is required", error_key="apiErrors.patrol.idempotencyRequired"
            )
        active_operations = await self._policy_reader.active_operations(
            require_fresh=True,
            now=utc_now(),
        )
        if active_operations.revision.policy.patrol.admission is PatrolAdmissionMode.PAUSED:
            raise ConflictError(
                "Ops Patrol is paused for new Runs",
                error_key="apiErrors.patrol.admissionPaused",
            )
        async with self._uow_factory() as uow:
            if automation_job is not None:
                requested_job = automation_job
                automation_job = await uow.scheduled_job.get_by_id(
                    requested_job.id,
                    for_update=True,
                )
                if automation_job is None or not automation_job.enabled:
                    raise ConflictError(
                        "Scheduled job is unavailable",
                        error_key="apiErrors.scheduling.jobUnavailable",
                    )
                automation_job.last_run_at = automation_fired_at
                automation_job.last_run_status = ScheduledRunStatus.RUNNING
                automation_job.last_run_error = None
                automation_job.next_run_at = requested_job.next_run_at
            existing = await uow.patrol.get_run_by_idempotency_key(idempotency_key)
            if existing is not None:
                owned = await uow.patrol.get_run(existing.id, scope)
                if owned is None or owned.pack_id != pack_id:
                    raise ConflictError(
                        "Idempotency key conflicts with another Run",
                        error_key="apiErrors.patrol.idempotencyConflict",
                    )
                return owned
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            if pack.status != PatrolPackStatus.ACTIVE:
                raise ConflictError(
                    "仅 active Pack 可触发", error_key="apiErrors.patrol.packNotActive"
                )
            if await uow.patrol.get_active_run_for_pack(pack_id) is not None:
                raise ConflictError(
                    "Pack 已有运行中的 Run", error_key="apiErrors.patrol.runAlreadyActive"
                )
            capability_hash = str(pack.validation_summary.get("capability_hash") or "")
            if not capability_hash:
                raise ConflictError(
                    "Pack 缺少已验证 capability hash",
                    error_key="apiErrors.patrol.packVersionNotValidated",
                )
            session = Session(
                title=f"[巡检] {pack.name}",
                owner_user_id=pack.owner_user_id,
                team_id=pack.team_id,
                mode=SessionMode.AGENT,
                status=SessionStatus.PENDING,
            )
            if automation_job is None:
                admitted_run_id = run_id_for_idempotency_key(idempotency_key)
                execution_run_id = admitted_run_id
            else:
                firing_id = automation_firing_id or idempotency_key
                automation_idempotency_key = f"scheduled:{automation_job.id}:{firing_id}"
                admitted_run_id = run_id_for_idempotency_key(automation_idempotency_key)
                execution_run_id = run_id_for_idempotency_key(f"child:{admitted_run_id}")
            run = PatrolRun(
                pack_id=pack.id,
                session_id=session.id,
                execution_run_id=execution_run_id,
                pack_version=pack.version,
                pack_snapshot={
                    "id": pack.id,
                    "name": pack.name,
                    "version": pack.version,
                    "owner_user_id": pack.owner_user_id,
                    "team_id": pack.team_id,
                    "mcp_server_id": pack.mcp_server_id,
                    "config": pack.config.model_dump(mode="json"),
                    "capability_hash": capability_hash,
                    "enabled_tools": pack.validation_summary.get("enabled_tools") or [],
                },
                trigger_type=trigger_type,
                idempotency_key=idempotency_key,
                collector_capability_hash=capability_hash,
            )
            run.submission_idempotency_key = f"{run.id}:{pack.version}"
            run.pack_snapshot["submission_idempotency_key"] = run.submission_idempotency_key
            session.status = SessionStatus.RUNNING
            run.status = PatrolRunStatus.RUNNING
            run.started_at = datetime.now(UTC)
            await uow.session.save(session)
            await uow.patrol.save_run(run)
            if automation_job is not None:
                automation_job.last_run_session_id = session.id
                await uow.scheduled_job.save(automation_job)
            if automation_job is None:
                execution_run_id = await self._run_admission.admit(
                    family=RunFamily.PATROL,
                    source_entity_type="patrol_run",
                    source_entity_id=run.id,
                    owner_scope=scope,
                    private_input={
                        "patrol_run_id": run.id,
                        "session_id": session.id,
                        "pack_id": pack.id,
                    },
                    public_input={
                        "session_id": session.id,
                        "pack_id": pack.id,
                    },
                    idempotency_key=idempotency_key,
                    run_id=admitted_run_id,
                    command_sink=uow.execution_commands,
                )
            else:
                automation_execution_run_id = await self._run_admission.admit(
                    family=RunFamily.AUTOMATION,
                    source_entity_type="scheduled_job",
                    source_entity_id=automation_job.id,
                    owner_scope=scope,
                    private_input={
                        "patrol_run_id": run.id,
                        "session_id": session.id,
                        "pack_id": pack.id,
                        "child_family": RunFamily.PATROL.value,
                        "child_source_entity_type": "patrol_run",
                        "child_source_entity_id": run.id,
                    },
                    public_input={
                        "firing_id": firing_id,
                        "session_id": session.id,
                        "patrol_run_id": run.id,
                    },
                    idempotency_key=automation_idempotency_key,
                    run_id=admitted_run_id,
                    command_sink=uow.execution_commands,
                )
                automation_job.last_execution_run_id = automation_execution_run_id
                await uow.scheduled_job.save(automation_job)
            await uow.commit()

        await self._audit(
            "patrol_run_triggered",
            run,
            actor_user_id,
            {
                "collector_server_id": run.pack_snapshot["mcp_server_id"],
                "capability_hash": run.collector_capability_hash,
                "enabled_tools": run.pack_snapshot["enabled_tools"],
                "trigger_type": trigger_type.value,
            },
        )
        return run

    async def get_run(self, run_id: str, scope: OwnerScope) -> PatrolRun:
        async with self._uow_factory() as uow:
            run = await uow.patrol.get_run(run_id, scope)
        if run is None:
            raise NotFoundError("Patrol Run 不存在", error_key="apiErrors.patrol.runNotFound")
        return run

    async def list_runs(self, scope: OwnerScope, **filters) -> list[PatrolRun]:
        async with self._uow_factory() as uow:
            return await uow.patrol.list_runs(scope, **filters)

    async def get_run_detail(
        self,
        run_id: str,
        scope: OwnerScope,
        *,
        review_actor_user_id: str | None = None,
    ) -> tuple[PatrolRun, list[PatrolCheckResult], list[PatrolFinding]]:
        review_started = False
        async with self._uow_factory() as uow:
            run = await uow.patrol.get_run(
                run_id,
                scope,
                for_update=review_actor_user_id is not None,
            )
            if run is None:
                raise NotFoundError("Patrol Run 不存在", error_key="apiErrors.patrol.runNotFound")
            if review_actor_user_id is not None and run.first_reviewed_at is None:
                run.first_reviewed_at = datetime.now(UTC)
                await uow.patrol.save_run(run)
                review_started = True
            results = await uow.patrol.list_check_results(run_id, scope)
            findings = await uow.patrol.list_findings(run_id, scope)
            if review_started:
                await uow.commit()
        if review_started:
            await self._audit("patrol_run_review_started", run, review_actor_user_id)
        return run, results, findings

    async def get_pack_metrics(
        self, pack_id: str, scope: OwnerScope
    ) -> dict[str, int | float | None]:
        """Return exact 30-day product metrics without marking Runs as reviewed."""
        created_from = datetime.now(UTC) - timedelta(days=30)
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            runs = await uow.patrol.list_runs(
                scope,
                pack_id=pack_id,
                created_from=created_from,
                limit=100,
                offset=0,
            )
            findings_by_run = {
                run.id: await uow.patrol.list_findings(run.id, scope) for run in runs
            }
        scheduled = [run for run in runs if run.trigger_type == PatrolTriggerType.SCHEDULE]
        scheduled_successes = [
            run
            for run in scheduled
            if run.status in {PatrolRunStatus.COMPLETED, PatrolRunStatus.COMPLETED_WITH_FINDINGS}
        ]
        findings = [item for items in findings_by_run.values() for item in items]
        review_durations: list[float] = []
        for run in runs:
            if run.first_reviewed_at is None:
                continue
            decided_at = [
                item.decided_at for item in findings_by_run[run.id] if item.decided_at is not None
            ]
            if not decided_at:
                continue
            duration = (max(decided_at) - run.first_reviewed_at).total_seconds() / 60
            if duration >= 0:
                review_durations.append(duration)
        return {
            "sample_size": len(runs),
            "scheduled_run_count": len(scheduled),
            "scheduled_success_rate": (
                len(scheduled_successes) / len(scheduled) if scheduled else None
            ),
            "finding_count": len(findings),
            "false_positive_count": sum(
                item.status == PatrolFindingStatus.FALSE_POSITIVE for item in findings
            ),
            "median_review_minutes": median(review_durations) if review_durations else None,
        }

    async def finalize_run(
        self,
        *,
        run_id: str,
        session_id: str,
        idempotency_key: str,
        collector_capability_hash: str,
        submissions: list[PatrolObservationSubmission],
        actor_user_id: str | None = None,
    ) -> PatrolRun:
        async with self._uow_factory() as uow:
            run = await uow.patrol.get_run(run_id, for_update=True)
            if run is None or run.session_id != session_id:
                raise ForbiddenError(
                    "Run 不属于当前 Session", error_key="apiErrors.patrol.runSessionMismatch"
                )
            if run.submission_idempotency_key != idempotency_key:
                raise ConflictError(
                    "提交幂等键不匹配", error_key="apiErrors.patrol.idempotencyConflict"
                )
            if run.status in {
                PatrolRunStatus.COMPLETED,
                PatrolRunStatus.COMPLETED_WITH_FINDINGS,
            }:
                return run
            if run.status == PatrolRunStatus.CANCELLED:
                raise ConflictError(
                    "已取消 Run 不接受结果", error_key="apiErrors.patrol.runCancelled"
                )
            if run.collector_capability_hash != collector_capability_hash:
                raise ValidationError(
                    "Collector capability hash 已变化",
                    error_key="apiErrors.patrol.collectorCapabilityMismatch",
                )
            if int(run.pack_snapshot.get("version", 0)) != run.pack_version:
                raise ConflictError(
                    "Run 的 Pack 版本快照不匹配", error_key="apiErrors.patrol.packVersionMismatch"
                )

            config = PatrolPackConfig.model_validate(run.pack_snapshot["config"])
            by_id: dict[str, PatrolObservationSubmission] = {}
            configured_ids = {check.id for check in config.checks}
            for submission in submissions:
                if submission.check_id not in configured_ids:
                    raise BadRequestError(
                        f"未知 Check: {submission.check_id}",
                        error_key="apiErrors.patrol.unknownCheck",
                    )
                if submission.check_id in by_id:
                    raise BadRequestError(
                        f"重复 Check: {submission.check_id}",
                        error_key="apiErrors.patrol.duplicateCheck",
                    )
                if len(submission.evidence_refs) > config.defaults.max_evidence_items:
                    raise BadRequestError(
                        "证据数量超限", error_key="apiErrors.patrol.evidenceLimit"
                    )
                if any(
                    item.target_ref and item.target_ref != config.target_ref
                    for item in submission.evidence_refs
                ):
                    raise ForbiddenError(
                        "证据目标与 Pack 不匹配",
                        error_key="apiErrors.patrol.evidenceTargetMismatch",
                    )
                observed_hash = hashlib.sha256(
                    json.dumps(
                        submission.observation,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
                for evidence in submission.evidence_refs:
                    # Collector evidence references are content-addressed over
                    # the canonical observation. Never trust an Agent-supplied
                    # verification flag or a merely well-shaped digest.
                    evidence.verified = bool(
                        hmac.compare_digest(evidence.sha256, observed_hash)
                        and evidence.ref.startswith("collector://evidence/")
                    )
                by_id[submission.check_id] = submission

            evaluated = [
                PatrolAssertionEngine.evaluate(check, by_id.get(check.id))
                for check in config.checks
            ]
            now = datetime.now(UTC)
            check_results: list[PatrolCheckResult] = []
            for item in evaluated:
                fingerprint = patrol_fingerprint(
                    run.pack_id,
                    item.check_id,
                    config.target_ref,
                    "",
                    item.assertion_results[0].assertion_id
                    if item.assertion_results
                    else item.error_code or "none",
                )
                check_results.append(
                    PatrolCheckResult(
                        run_id=run.id,
                        check_id=item.check_id,
                        status=item.status,
                        severity=item.severity,
                        observed=item.observed,
                        assertion_results=[
                            result.model_dump(mode="json") for result in item.assertion_results
                        ],
                        evidence_refs=[
                            ref.model_dump(mode="json", exclude={"verified"})
                            for ref in item.evidence_refs
                        ],
                        explanation=item.explanation,
                        error_code=item.error_code,
                        error_message=item.error_message,
                        fingerprint=fingerprint,
                        started_at=run.started_at or run.created_at,
                        finished_at=now,
                    )
                )
            await uow.patrol.save_check_results(check_results)

            # A remediation recheck answers only whether its linked Finding
            # was fixed. The recheck_run_id back-reference prevents an
            # unrelated matching fingerprint from closing another Finding.
            remediation_for_recheck = None
            remediation_recheck_outcome: str | None = None
            if run.trigger_type == PatrolTriggerType.REMEDIATION:
                remediation_for_recheck = await uow.patrol.get_remediation_by_recheck_run_id(run.id)
                if (
                    remediation_for_recheck is not None
                    and remediation_for_recheck.status == PatrolRemediationStatus.EXECUTED
                ):
                    original_results = await uow.patrol.list_check_results(
                        remediation_for_recheck.run_id
                    )
                    original_check = next(
                        (
                            item
                            for item in original_results
                            if item.id == remediation_for_recheck.check_result_id
                        ),
                        None,
                    )
                    recheck_result = (
                        next(
                            (
                                item
                                for item in check_results
                                if item.check_id == original_check.check_id
                            ),
                            None,
                        )
                        if original_check is not None
                        else None
                    )
                    if recheck_result is not None:
                        if recheck_result.status == PatrolCheckStatus.PASS:
                            original_finding = await uow.patrol.get_finding(
                                remediation_for_recheck.finding_id, for_update=True
                            )
                            if original_finding is not None and original_finding.status in {
                                PatrolFindingStatus.OPEN,
                                PatrolFindingStatus.ACKNOWLEDGED,
                            }:
                                original_finding.status = PatrolFindingStatus.RESOLVED
                                original_finding.decided_by = "system:remediation"
                                original_finding.decided_at = now
                                original_finding.decision_reason = f"Auto-resolved: remediation {remediation_for_recheck.id} recheck run {run.id} passed"
                                await uow.patrol.save_finding(original_finding)
                            remediation_for_recheck.status = PatrolRemediationStatus.VERIFIED
                            await uow.patrol.save_remediation(remediation_for_recheck)
                            remediation_recheck_outcome = "verified"
                            # Verification is a distinct transition owned by
                            # the recheck Run rather than the Actuator call.
                            self._governance_metrics.record_remediation_transition("verified")
                        else:
                            remediation_for_recheck.status = PatrolRemediationStatus.FAILED
                            remediation_for_recheck.error_code = "recheck_failed"
                            remediation_for_recheck.error_message = f"Recheck run {run.id} still reports {original_check.check_id}: {recheck_result.status.value}"
                            await uow.patrol.save_remediation(remediation_for_recheck)
                            remediation_recheck_outcome = "failed"
                            # Recheck failure is likewise a distinct durable
                            # transition and must emit its own metric.
                            self._governance_metrics.record_remediation_transition("failed")

            for result in check_results:
                if result.status not in {
                    PatrolCheckStatus.WARN,
                    PatrolCheckStatus.FAIL,
                    PatrolCheckStatus.ERROR,
                }:
                    continue
                current = await uow.patrol.get_open_finding_by_fingerprint(result.fingerprint)
                if current:
                    current.run_id = run.id
                    current.check_result_id = result.id
                    current.last_seen_at = now
                    current.occurrence_count += 1
                    current.severity = result.severity
                    current.summary = (
                        result.explanation
                        or result.error_message
                        or f"{result.check_id}: {result.status.value}"
                    )
                    await uow.patrol.save_finding(current)
                else:
                    await uow.patrol.save_finding(
                        PatrolFinding(
                            run_id=run.id,
                            check_result_id=result.id,
                            fingerprint=result.fingerprint,
                            severity=result.severity,
                            title=f"{result.check_id}: {result.status.value.upper()}",
                            summary=result.explanation
                            or result.error_message
                            or f"Check {result.check_id} requires review",
                            first_seen_at=now,
                            last_seen_at=now,
                        )
                    )

            run = run.finalize(evaluated, now)
            await uow.patrol.save_run(run)
            await uow.session.update_status(session_id, SessionStatus.COMPLETED)
            await uow.commit()
        await self._audit(
            "patrol_result_submitted",
            run,
            actor_user_id,
            {"result_count": len(submissions)},
        )
        await self._audit("patrol_run_finalized", run, actor_user_id, run.summary)
        if remediation_recheck_outcome is not None and remediation_for_recheck is not None:
            await self._audit(
                "patrol_remediation_recheck_completed",
                run,
                actor_user_id,
                {
                    "remediation_id": remediation_for_recheck.id,
                    "outcome": remediation_recheck_outcome,
                },
            )
        await self._materialize_outputs(run)
        return run

    async def _materialize_outputs(self, run: PatrolRun) -> None:
        """Create deterministic outputs after the authoritative DB transaction."""
        try:
            async with self._uow_factory() as uow:
                results = await uow.patrol.list_check_results(run.id)
                findings = await uow.patrol.list_findings(run.id)
                pack = await uow.patrol.get_pack(run.pack_id)
            if self._artifact_service is not None and run.session_id:
                body = PatrolReportService.render(run, results, findings)
                artifact = await self._artifact_service.write_content(
                    session_id=run.session_id,
                    artifact_id=None,
                    kind="doc",
                    title=f"patrol-{run.id}.md",
                    content=body,
                )
                await self._artifact_service.finalize(run.session_id, artifact.id)
                run.report_artifact_id = artifact.id
                async with self._uow_factory() as uow:
                    await uow.patrol.save_run(run)
                    await uow.commit()
            self._governance_metrics.observe_patrol_finalized(run, results, findings)
            if self._notification_service is not None and pack is not None:
                await self._notification_service.send(
                    pack.owner_user_id,
                    "patrol_complete",
                    f'Patrol "{pack.name}" completed: {run.status.value}; findings={run.warn_count + run.fail_count + run.error_count}',
                    i18n_key="notifications.patrolCompleted",
                    i18n_params={"packName": pack.name, "status": run.status.value},
                    session_id=run.session_id,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("Patrol output materialization failed run=%s", run.id)
            run.summary = {**run.summary, "output_error": str(exc)[:1000]}
            try:
                async with self._uow_factory() as uow:
                    await uow.patrol.save_run(run)
                    await uow.commit()
            except (OSError, RuntimeError, ValueError):
                logger.exception("Failed to persist Patrol output error run=%s", run.id)

    @staticmethod
    async def _abort_remediation_recheck(
        uow: IUnitOfWork, run: PatrolRun
    ) -> PatrolRemediation | None:
        """When a REMEDIATION-triggered recheck Run terminates without ever
        reaching finalize_run's normal completion path (worker crash, run
        timeout, or an operator cancelling it), the remediation that
        dispatched it is otherwise stuck in EXECUTED forever: only
        finalize_run's recheck closure ever moves EXECUTED to VERIFIED
        (recheck passed) or FAILED(recheck_failed) (recheck completed but
        the check still fails), and this run will never reach finalize_run.
        A remediation stuck in EXECUTED blocks any new proposal for the same
        Finding (EXECUTED is not in PATROL_REMEDIATION_TERMINAL_STATUSES, and
        the DB partial unique index behind get_active_remediation_for_finding
        agrees). Called from within the caller's own uow transaction
        (mark_run_failed / cancel_run) so this participates in the same
        atomic write as the run's own terminal-status transition.
        """
        if run.trigger_type != PatrolTriggerType.REMEDIATION:
            return None
        remediation = await uow.patrol.get_remediation_by_recheck_run_id(run.id)
        if remediation is None or remediation.status != PatrolRemediationStatus.EXECUTED:
            return None
        remediation.status = PatrolRemediationStatus.FAILED
        remediation.error_code = "recheck_aborted"
        remediation.error_message = (
            f"Recheck run {run.id} terminated ({run.status.value}) before it could complete"
        )
        await uow.patrol.save_remediation(remediation)
        return remediation

    async def mark_run_failed(
        self,
        session_id: str,
        *,
        error_code: str = "RESULT_SUBMISSION_MISSING",
        error_message: str = "Agent terminated without a complete Patrol submission",
    ) -> PatrolRun | None:
        """Fail an unfinished Patrol Run when its Agent session terminates."""
        async with self._uow_factory() as uow:
            run = await uow.patrol.get_run_by_session_id(session_id)
            if run is None or run.status not in {
                PatrolRunStatus.QUEUED,
                PatrolRunStatus.RUNNING,
            }:
                return run
            run.status = PatrolRunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            started = run.started_at or run.created_at
            run.duration_ms = max(0, int((run.finished_at - started).total_seconds() * 1000))
            config = PatrolPackConfig.model_validate(run.pack_snapshot["config"])
            existing_results = await uow.patrol.list_check_results(run.id)
            existing_ids = {item.check_id for item in existing_results}
            missing_results = [
                PatrolCheckResult(
                    run_id=run.id,
                    check_id=check.id,
                    status=PatrolCheckStatus.ERROR,
                    severity="warning",
                    error_code=error_code,
                    error_message=error_message[:2000],
                    explanation=("Run terminated before this enabled check submitted a result"),
                    fingerprint=patrol_fingerprint(
                        run.pack_id,
                        check.id,
                        config.target_ref,
                        "",
                        error_code,
                    ),
                    started_at=started,
                    finished_at=run.finished_at,
                )
                for check in config.checks
                if check.enabled and check.id not in existing_ids
            ]
            if missing_results:
                await uow.patrol.save_check_results(missing_results)
                for result in missing_results:
                    await uow.patrol.save_finding(
                        PatrolFinding(
                            run_id=run.id,
                            check_result_id=result.id,
                            fingerprint=result.fingerprint,
                            severity=result.severity,
                            title=f"{result.check_id}: ERROR",
                            summary=result.explanation,
                            first_seen_at=run.finished_at,
                            last_seen_at=run.finished_at,
                        )
                    )
            counts = Counter(item.status.value for item in [*existing_results, *missing_results])
            run.pass_count = counts["pass"]
            run.warn_count = counts["warn"]
            run.fail_count = counts["fail"]
            run.error_count = counts["error"]
            run.skipped_count = counts["skipped"]
            run.evidence_completeness = 0.0
            run.summary = {
                "error_code": error_code,
                "error_message": error_message[:2000],
                "counts": {
                    key: counts[key] for key in ("pass", "warn", "fail", "error", "skipped")
                },
                "evidence_completeness": 0.0,
            }
            await uow.patrol.save_run(run)
            aborted_remediation = await self._abort_remediation_recheck(uow, run)
            await uow.commit()
        await self._audit("patrol_run_finalized", run, None, run.summary)
        if aborted_remediation is not None:
            await self._audit(
                "patrol_remediation_recheck_completed",
                run,
                None,
                {"remediation_id": aborted_remediation.id, "outcome": "aborted"},
            )
        await self._materialize_outputs(run)
        return run

    async def replay_run(
        self,
        run_id: str,
        scope: OwnerScope,
        actor_user_id: str,
    ) -> PatrolRun:
        if not self._fixture_replay_enabled:
            raise ForbiddenError(
                "Patrol replay is disabled", error_key="apiErrors.patrol.replayDisabled"
            )
        original = await self.get_run(run_id, scope)
        return await self.trigger_pack(
            original.pack_id,
            scope,
            actor_user_id,
            idempotency_key=f"replay:{original.id}:{datetime.now(UTC).isoformat()}",
            trigger_type=PatrolTriggerType.REPLAY,
        )

    async def cancel_run(self, run_id: str, scope: OwnerScope, actor_user_id: str) -> PatrolRun:
        async with self._uow_factory() as uow:
            run = await uow.patrol.get_run(run_id, scope, for_update=True)
            if run is None:
                raise NotFoundError("Patrol Run 不存在", error_key="apiErrors.patrol.runNotFound")
            if run.status not in {PatrolRunStatus.QUEUED, PatrolRunStatus.RUNNING}:
                raise ConflictError(
                    "仅 queued/running Run 可取消",
                    error_key="apiErrors.patrol.cancelStateConflict",
                )
            if run.execution_run_id is None:
                raise ConflictError(
                    "Patrol Run has no formal execution",
                    error_key="apiErrors.patrol.executionRunMissing",
                )
            formal_run_id = run.execution_run_id
            run.status = PatrolRunStatus.CANCELLED
            run.finished_at = datetime.now(UTC)
            await uow.patrol.save_run(run)
            aborted_remediation = await self._abort_remediation_recheck(uow, run)
            if run.session_id:
                await uow.session.update_status(run.session_id, SessionStatus.CANCELLED)
            await self._commands.submit(
                RegisteredCommand(
                    command_id=uuid5(
                        NAMESPACE_URL,
                        f"opencitadel:patrol-cancel:{run.id}",
                    ),
                    command_type="CancelRun",
                    run_id=formal_run_id,
                    payload={"reason": "patrol_cancelled"},
                ),
                CommandContext(
                    owner_user_id=None if scope.team_id else scope.user_id,
                    team_id=scope.team_id,
                    correlation_id=formal_run_id,
                    causation_id=None,
                    issued_at=datetime.now(UTC),
                ),
                sink=uow.execution_commands,
            )
            await uow.commit()
        await self._audit("patrol_run_cancelled", run, actor_user_id)
        if aborted_remediation is not None:
            await self._audit(
                "patrol_remediation_recheck_completed",
                run,
                actor_user_id,
                {"remediation_id": aborted_remediation.id, "outcome": "aborted"},
            )
        return run

    async def decide_finding(
        self,
        finding_id: str,
        scope: OwnerScope,
        actor_user_id: str,
        status: PatrolFindingStatus,
        reason: str = "",
    ) -> PatrolFinding:
        if status == PatrolFindingStatus.FALSE_POSITIVE and not reason.strip():
            raise BadRequestError(
                "false-positive 必须填写原因", error_key="apiErrors.patrol.reasonRequired"
            )
        async with self._uow_factory() as uow:
            finding = await uow.patrol.get_finding(finding_id, scope, for_update=True)
            if finding is None:
                raise NotFoundError(
                    "Patrol Finding 不存在", error_key="apiErrors.patrol.findingNotFound"
                )
            finding.status = status
            finding.decided_by = actor_user_id
            finding.decided_at = datetime.now(UTC)
            finding.decision_reason = reason.strip() or None
            await uow.patrol.save_finding(finding)
            run = await uow.patrol.get_run(finding.run_id)
            await uow.commit()
        if run:
            await self._audit(
                "patrol_finding_decided",
                run,
                actor_user_id,
                {"finding_id": finding.id, "decision": status.value, "reason": reason},
            )
        return finding
