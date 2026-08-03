"""Owner-scoped Patrol Pack lifecycle and validation."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.application.errors.exceptions import BadRequestError, ConflictError, NotFoundError, ValidationError
from app.application.services.audit_service import AuditService
from app.application.services.config_provider import get_runtime_config
from app.domain.models.audit_log import AuditLog
from app.domain.models.integration_server import MCPServerRecord
from app.domain.models.patrol import PatrolPack, PatrolPackConfig, PatrolPackStatus
from app.domain.models.scheduled_job import ScheduledJob
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.models.tool_policy import ApprovalMode, ToolCapability, ToolEffect, ToolIdempotency
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.schedule_utils import compute_next_run


class PatrolCollectorValidatorPort(Protocol):
    async def get_capabilities(self, server: MCPServerRecord) -> dict[str, Any]: ...
    async def dry_run(self, server: MCPServerRecord, config: PatrolPackConfig) -> dict[str, Any]: ...


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "patrol-pack"


class PatrolPackService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        audit_service: AuditService | None = None,
        collector_validator: PatrolCollectorValidatorPort | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._audit_service = audit_service
        self._collector_validator = collector_validator

    @staticmethod
    def _require_enabled() -> None:
        if not get_runtime_config().feature_flags.enable_ops_patrol:
            raise BadRequestError("Ops Patrol is disabled", error_key="patrol.disabled")

    async def _audit(self, action: str, pack: PatrolPack, actor_user_id: str, metadata: dict | None = None) -> None:
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
        skill_id: str | None = None,
        slug: str | None = None,
    ) -> PatrolPack:
        self._require_enabled()
        async with self._uow_factory() as uow:
            server = await uow.mcp_server.get_by_id(mcp_server_id, scope=scope)
            if server is None:
                raise NotFoundError("Collector 不存在或不可访问", error_key="patrol.targetScopeDenied")
            skill = await uow.skill.get_by_slug("ops-patrol") if skill_id is None else await uow.skill.get_by_id(skill_id, scope=scope)
            if skill is None or skill.slug != "ops-patrol":
                raise BadRequestError("内置 Ops Patrol Skill 尚未初始化", error_key="patrol.skillMissing")
            pack = PatrolPack(
                owner_user_id=owner_user_id,
                team_id=scope.team_id if scope.type == OwnerScopeType.TEAM else None,
                name=name,
                slug=_slugify(slug or name),
                config=config,
                mcp_server_id=mcp_server_id,
                skill_id=skill.id,
            )
            job = ScheduledJob(
                name=f"[巡检] {name}",
                owner_user_id=owner_user_id,
                team_id=pack.team_id,
                trigger_type="cron",
                trigger_spec=config.schedule.cron,
                prompt_template="",
                skill_id=skill.id,
                operator_scope="owned",
                gate_profile="strict",
                enabled=False,
                timezone=config.timezone,
                source_type="patrol_pack",
                source_id=pack.id,
                next_run_at=compute_next_run("cron", config.schedule.cron, timezone_name=config.timezone),
            )
            pack.scheduled_job_id = job.id
            await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
        await self._audit("patrol_pack_created", pack, owner_user_id)
        return pack

    async def get_pack(self, pack_id: str, scope: OwnerScope) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope)
        if pack is None:
            raise NotFoundError("Patrol Pack 不存在", error_key="patrol.packNotFound")
        return pack

    async def list_packs(self, scope: OwnerScope, *, limit: int = 20, offset: int = 0) -> list[PatrolPack]:
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
        self._require_enabled()
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="patrol.packNotFound")
            if pack.version != expected_version:
                raise ConflictError("Pack 版本已变化", error_key="patrol.versionConflict")
            if mcp_server_id is not None:
                if await uow.mcp_server.get_by_id(mcp_server_id, scope=scope) is None:
                    raise NotFoundError("Collector 不存在或不可访问", error_key="patrol.targetScopeDenied")
                pack.mcp_server_id = mcp_server_id
            if name is not None:
                pack.name = name
            if config is not None:
                pack.config = config
            pack.version += 1
            pack.status = PatrolPackStatus.DRAFT
            pack.last_validated_at = None
            pack.last_validated_version = None
            pack.validation_summary = {}
            pack.updated_at = datetime.now(timezone.utc)
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = False
                    job.name = f"[巡检] {pack.name}"
                    job.trigger_type = "cron"
                    job.trigger_spec = pack.config.schedule.cron
                    job.timezone = pack.config.timezone
                    job.next_run_at = compute_next_run("cron", job.trigger_spec, timezone_name=job.timezone)
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
        await self._audit("patrol_pack_updated", pack, actor_user_id)
        return pack

    @staticmethod
    def _validate_static(config: PatrolPackConfig, server: MCPServerRecord) -> list[str]:
        errors: list[str] = []
        if not server.enabled:
            errors.append("Collector is disabled")
        required_tools = {"get_capabilities", *(check.probe.tool for check in config.checks if check.enabled)}
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

    async def validate_pack(self, pack_id: str, scope: OwnerScope, actor_user_id: str) -> PatrolPack:
        self._require_enabled()
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="patrol.packNotFound")
            pack.status = PatrolPackStatus.VALIDATING
            await uow.patrol.save_pack(pack)
            server = await uow.mcp_server.get_by_id(pack.mcp_server_id, scope=scope)
            if server is None:
                raise NotFoundError("Collector 不存在或不可访问", error_key="patrol.targetScopeDenied")

            errors = self._validate_static(pack.config, server)
            capabilities: dict[str, Any] = {}
            dry_run: dict[str, Any] = {}
            if not errors:
                if self._collector_validator is None:
                    embedded = server.extra.get("patrol_capabilities") if isinstance(server.extra, dict) else None
                    if not isinstance(embedded, dict):
                        errors.append("Collector live capability validator is unavailable")
                    else:
                        capabilities = embedded
                else:
                    try:
                        capabilities = await self._collector_validator.get_capabilities(server)
                        dry_run = await self._collector_validator.dry_run(server, pack.config)
                        if not dry_run.get("ok"):
                            errors.append("Collector read-only dry run did not pass")
                    except Exception as exc:
                        errors.append(f"Collector unavailable: {str(exc)[:500]}")

            enabled_tools = set(capabilities.get("enabled_tools") or [])
            required = {check.probe.tool for check in pack.config.checks if check.enabled}
            if capabilities and not required.issubset(enabled_tools):
                errors.append(f"Collector capabilities missing: {sorted(required - enabled_tools)}")
            schema_hashes = capabilities.get("output_schema_hashes") or {}
            for check in pack.config.checks:
                if check.enabled and capabilities and schema_hashes.get(check.probe.tool) != check.probe.output_schema_hash:
                    errors.append(f"output schema hash mismatch: {check.id}")

            pack.last_validated_at = datetime.now(timezone.utc)
            pack.validation_summary = {
                "ok": not errors,
                "errors": errors,
                "capability_hash": capabilities.get("overall_capability_hash"),
                "enabled_tools": sorted(enabled_tools),
                "dry_run": dry_run,
            }
            if errors:
                pack.status = PatrolPackStatus.INVALID
                pack.last_validated_version = None
            else:
                pack.status = PatrolPackStatus.DRAFT
                pack.last_validated_version = pack.version
            pack.updated_at = datetime.now(timezone.utc)
            await uow.patrol.save_pack(pack)
        await self._audit("patrol_pack_validated", pack, actor_user_id, {"ok": not errors, "errors": errors})
        return pack

    async def activate_pack(self, pack_id: str, scope: OwnerScope, actor_user_id: str) -> PatrolPack:
        self._require_enabled()
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="patrol.packNotFound")
            if pack.last_validated_version != pack.version or not pack.validation_summary.get("ok"):
                raise ConflictError("当前 Pack 版本尚未通过验证", error_key="patrol.packVersionNotValidated")
            pack.status = PatrolPackStatus.ACTIVE
            pack.updated_at = datetime.now(timezone.utc)
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = pack.config.schedule.enabled
                    job.next_run_at = compute_next_run("cron", job.trigger_spec, timezone_name=job.timezone)
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
        await self._audit("patrol_pack_activated", pack, actor_user_id)
        return pack

    async def pause_pack(self, pack_id: str, scope: OwnerScope, actor_user_id: str) -> PatrolPack:
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="patrol.packNotFound")
            pack.status = PatrolPackStatus.PAUSED
            pack.updated_at = datetime.now(timezone.utc)
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = False
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
        await self._audit("patrol_pack_paused", pack, actor_user_id)
        return pack

    async def delete_pack(self, pack_id: str, scope: OwnerScope, actor_user_id: str) -> None:
        self._require_enabled()
        async with self._uow_factory() as uow:
            pack = await uow.patrol.get_pack(pack_id, scope, for_update=True)
            if pack is None:
                raise NotFoundError("Patrol Pack 不存在", error_key="patrol.packNotFound")
            if pack.status not in {PatrolPackStatus.DRAFT, PatrolPackStatus.PAUSED, PatrolPackStatus.INVALID}:
                raise ConflictError("仅 draft、invalid 或 paused Pack 可删除", error_key="patrol.deleteStateConflict")
            pack.deleted_at = datetime.now(timezone.utc)
            pack.updated_at = pack.deleted_at
            if pack.scheduled_job_id:
                job = await uow.scheduled_job.get_by_id(pack.scheduled_job_id, scope=scope)
                if job:
                    job.enabled = False
                    await uow.scheduled_job.save(job)
            await uow.patrol.save_pack(pack)
        await self._audit("patrol_pack_deleted", pack, actor_user_id)
