"""Owner-scoped Patrol Pack lifecycle and validation."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.application.execution.admission import RunAdmissionService
from app.application.services.audit_service import AuditService
from app.domain.errors import ConflictError, NotFoundError
from app.domain.execution.run import RunFamily
from app.domain.models.audit_log import AuditLog
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.patrol import PatrolPack, PatrolPackConfig, PatrolPackStatus
from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.tool_policy import ApprovalMode, ToolCapability, ToolEffect, ToolIdempotency
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.schedule_utils import compute_next_run


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "patrol-pack"


class PatrolPackService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService | None = None,
        run_admission_service: RunAdmissionService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._run_admission = run_admission_service

    async def _audit(
        self, action: str, pack: PatrolPack, actor_user_id: str, metadata: dict | None = None
    ) -> None:
        if self._audit_service is None:
            return
        await self._audit_service.record(
            AuditLog(
                actor_user_id=actor_user_id,
                action=action,
                resource_type="patrol_pack",
                resource_id=pack.id,
                team_id=pack.team_id,
                metadata={"version": pack.version, "status": pack.status.value, **(metadata or {})},
            )
        )

    async def create_pack(
        self,
        *,
        owner_user_id: str,
        scope: OwnerScope,
        name: str,
        mcp_server_id: str,
        config: PatrolPackConfig,
        slug: str | None = None,
    ) -> PatrolPack:
        async with self._uow_factory() as uow:
            server = await uow.mcp_server.get_by_id(mcp_server_id, scope=scope)
            if server is None:
                raise NotFoundError(
                    "Collector 不存在或不可访问", error_key="apiErrors.patrol.targetScopeDenied"
                )
            pack = PatrolPack(
                owner_user_id=owner_user_id,
                team_id=scope.team_id if scope.type == OwnerScopeType.TEAM else None,
                name=name,
                slug=_slugify(slug or name),
                config=config,
                mcp_server_id=mcp_server_id,
            )
            job = ScheduledJob(
                name=f"[巡检] {name}",
                owner_user_id=owner_user_id,
                team_id=pack.team_id,
                trigger_type="cron",
                trigger_spec=config.schedule.cron,
                prompt_template="",
                skill_id=None,
                enabled=False,
                timezone=config.timezone,
                source_type="patrol_pack",
                source_id=pack.id,
                next_run_at=compute_next_run(
                    "cron", config.schedule.cron, timezone_name=config.timezone
                ),
            )
            pack.scheduled_job_id = job.id
            await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit("patrol_pack_created", pack, owner_user_id)
        return pack

    async def get_pack(self, pack_id: str, scope: OwnerScope) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope)
        if pack is None:
            raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
        return pack

    async def list_packs(
        self, scope: OwnerScope, *, limit: int = 20, offset: int = 0
    ) -> list[PatrolPack]:
        async with self._uow_factory() as uow:
            return await uow.patrol.list_packs(scope, limit=limit, offset=offset)

    async def patch_pack(
        self,
        pack_id: str,
        scope: OwnerScope,
        actor_user_id: str,
        *,
        expected_version: int,
        name: str | None = None,
        config: PatrolPackConfig | None = None,
        mcp_server_id: str | None = None,
    ) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            if pack.version != expected_version:
                raise ConflictError("Pack 版本已变化", error_key="apiErrors.patrol.versionConflict")
            if mcp_server_id is not None:
                if await uow.mcp_server.get_by_id(mcp_server_id, scope=scope) is None:
                    raise NotFoundError(
                        "Collector 不存在或不可访问", error_key="apiErrors.patrol.targetScopeDenied"
                    )
                pack.mcp_server_id = mcp_server_id
            if name is not None:
                pack.name = name
            if config is not None:
                pack.config = config
            pack.version += 1
            pack.status = PatrolPackStatus.DRAFT
            pack.last_validated_at = None
            pack.last_validated_version = None
            pack.validation_run_id = None
            pack.validation_summary = {}
            pack.updated_at = datetime.now(UTC)
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = False
                    job.name = f"[巡检] {pack.name}"
                    job.trigger_type = "cron"
                    job.trigger_spec = pack.config.schedule.cron
                    job.timezone = pack.config.timezone
                    job.next_run_at = compute_next_run(
                        "cron", job.trigger_spec, timezone_name=job.timezone
                    )
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit("patrol_pack_updated", pack, actor_user_id)
        return pack

    @staticmethod
    def _validate_static(config: PatrolPackConfig, server: MCPServerRecord) -> list[str]:
        errors: list[str] = []
        if not server.enabled:
            errors.append("Collector is disabled")
        required_tools = {
            "get_capabilities",
            *(check.probe.tool for check in config.checks if check.enabled),
        }
        for tool_name in sorted(required_tools):
            policy = server.tool_policies.get(tool_name)
            if policy is None:
                errors.append(f"Collector tool policy missing: {tool_name}")
                continue
            if (
                policy.capability != ToolCapability.INTEGRATION_READ
                or policy.effect != ToolEffect.READ_ONLY
                or policy.idempotency != ToolIdempotency.SAFE
                or policy.approval != ApprovalMode.NEVER
            ):
                errors.append(f"Collector tool is not fixed read-only: {tool_name}")
        for check in config.checks:
            namespace = check.probe.args.get("namespace")
            if namespace is not None and namespace not in config.scope.namespaces:
                errors.append(f"namespace outside Pack scope: {namespace}")
            forbidden = {"url", "promql", "query", "command", "script"} & set(check.probe.args)
            if forbidden:
                errors.append(f"raw probe arguments are forbidden: {sorted(forbidden)}")
        return errors

    async def request_validation(
        self,
        pack_id: str,
        scope: OwnerScope,
        actor_user_id: str,
    ) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            if pack.status == PatrolPackStatus.VALIDATING:
                raise ConflictError(
                    "Pack validation is already running",
                    error_key="apiErrors.patrol.validationAlreadyRunning",
                )
            server = await uow.mcp_server.get_by_id(pack.mcp_server_id, scope=scope)
            if server is None:
                raise NotFoundError(
                    "Collector 不存在或不可访问", error_key="apiErrors.patrol.targetScopeDenied"
                )
            validated_version = pack.version
            errors = self._validate_static(pack.config, server)
            if errors:
                pack.status = PatrolPackStatus.INVALID
                pack.last_validated_at = datetime.now(UTC)
                pack.last_validated_version = None
                pack.validation_run_id = None
                pack.validation_summary = {
                    "ok": False,
                    "errors": errors,
                    "capability_hash": None,
                    "enabled_tools": [],
                    "dry_run": {},
                }
            else:
                if self._run_admission is None:
                    raise RuntimeError("Patrol validation Run admission is unavailable")
                validation_run_id = uuid4()
                pack.status = PatrolPackStatus.VALIDATING
                pack.last_validated_at = None
                pack.last_validated_version = None
                pack.validation_run_id = str(validation_run_id)
                pack.validation_summary = {
                    "errors": [],
                    "validation_run_id": str(validation_run_id),
                }
                await self._run_admission.admit(
                    family=RunFamily.PATROL,
                    source_entity_type="patrol_pack_validation",
                    source_entity_id=pack.id,
                    owner_scope=scope,
                    private_input={
                        "pack_id": pack.id,
                        "pack_version": validated_version,
                        "validation_run_id": str(validation_run_id),
                        "actor_user_id": actor_user_id,
                    },
                    public_input={
                        "pack_id": pack.id,
                        "pack_version": validated_version,
                        "operation": "validate",
                    },
                    workflow={
                        "operation": "validate",
                        "pack_id": pack.id,
                        "pack_version": validated_version,
                        "validation_run_id": str(validation_run_id),
                    },
                    idempotency_key=f"patrol-pack-validation:{pack.id}:{validation_run_id}",
                    run_id=validation_run_id,
                    command_sink=uow.execution_commands,
                )
            pack.updated_at = datetime.now(UTC)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit(
            "patrol_pack_validated" if errors else "patrol_pack_validation_requested",
            pack,
            actor_user_id,
            {"ok": False if errors else None, "errors": errors},
        )
        return pack

    async def complete_validation(
        self,
        *,
        pack_id: str,
        scope: OwnerScope,
        actor_user_id: str,
        validation_run_id: str,
        validated_version: int,
        capabilities: dict[str, Any],
        dry_run: dict[str, Any],
        errors: list[str],
    ) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            if (
                pack.version != validated_version
                or pack.validation_run_id != validation_run_id
                or pack.status != PatrolPackStatus.VALIDATING
            ):
                raise ConflictError(
                    "Pack validation result is stale",
                    error_key="apiErrors.patrol.versionConflict",
                )

            resolved_errors = list(errors)
            if not dry_run.get("ok") and not resolved_errors:
                resolved_errors.append("Collector read-only dry run did not pass")
            enabled_tools = set(capabilities.get("enabled_tools") or [])
            required = {check.probe.tool for check in pack.config.checks if check.enabled}
            if capabilities and not required.issubset(enabled_tools):
                resolved_errors.append(
                    f"Collector capabilities missing: {sorted(required - enabled_tools)}"
                )
            schema_hashes = capabilities.get("output_schema_hashes") or {}
            resolved_errors.extend(
                f"output schema hash mismatch: {check.id}"
                for check in pack.config.checks
                if check.enabled
                and capabilities
                and schema_hashes.get(check.probe.tool) != check.probe.output_schema_hash
            )

            pack.last_validated_at = datetime.now(UTC)
            pack.validation_run_id = None
            pack.validation_summary = {
                "ok": not resolved_errors,
                "errors": resolved_errors,
                "validation_run_id": validation_run_id,
                "capability_hash": capabilities.get("overall_capability_hash"),
                "enabled_tools": sorted(enabled_tools),
                "dry_run": dry_run,
            }
            if resolved_errors:
                pack.status = PatrolPackStatus.INVALID
                pack.last_validated_version = None
            else:
                pack.status = PatrolPackStatus.DRAFT
                pack.last_validated_version = pack.version
            pack.updated_at = datetime.now(UTC)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit(
            "patrol_pack_validated",
            pack,
            actor_user_id,
            {"ok": not resolved_errors, "errors": resolved_errors},
        )
        return pack

    async def activate_pack(
        self, pack_id: str, scope: OwnerScope, actor_user_id: str
    ) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            if pack.last_validated_version != pack.version or not pack.validation_summary.get("ok"):
                raise ConflictError(
                    "当前 Pack 版本尚未通过验证",
                    error_key="apiErrors.patrol.packVersionNotValidated",
                )
            pack.status = PatrolPackStatus.ACTIVE
            pack.updated_at = datetime.now(UTC)
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = pack.config.schedule.enabled
                    job.next_run_at = compute_next_run(
                        "cron", job.trigger_spec, timezone_name=job.timezone
                    )
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit("patrol_pack_activated", pack, actor_user_id)
        return pack

    async def pause_pack(self, pack_id: str, scope: OwnerScope, actor_user_id: str) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            pack.status = PatrolPackStatus.PAUSED
            pack.updated_at = datetime.now(UTC)
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = False
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit("patrol_pack_paused", pack, actor_user_id)
        return pack

    async def delete_pack(self, pack_id: str, scope: OwnerScope, actor_user_id: str) -> None:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="apiErrors.patrol.packNotFound")
            if pack.status not in {
                PatrolPackStatus.DRAFT,
                PatrolPackStatus.PAUSED,
                PatrolPackStatus.INVALID,
            }:
                raise ConflictError(
                    "仅 draft、invalid 或 paused Pack 可删除",
                    error_key="apiErrors.patrol.deleteStateConflict",
                )
            pack.deleted_at = datetime.now(UTC)
            pack.updated_at = pack.deleted_at
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = False
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
            await uow.commit()
        await self._audit("patrol_pack_deleted", pack, actor_user_id)
