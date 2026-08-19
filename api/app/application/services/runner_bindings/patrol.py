#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Ops Patrol session-binding resolution.

Moved out of task_runner_factory.py verbatim (phase-4 engineering-quality
Task 3) — behavior-preserving extraction, not a rewrite. See
TaskRunnerFactory.create_runner for how resolve_patrol_binding's result
(patrol_server_name, agent_config) is threaded into the rest of runner
construction (mcp_config filtering, extra_tools, a2a_config exclusion).
"""
from typing import Callable, Optional

from app.domain.errors import NotFoundError
from app.application.services.patrol_collector_validator import MCPPatrolCollectorValidator
from app.domain.external.connection_pool import MCPConnectionPoolPort
from app.domain.models.app_config import AgentConfig
from app.domain.models.patrol import PATROL_PROBE_TOOLS, PatrolPackConfig, PatrolPackStatus, PatrolRun
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.repositories.uow import IUnitOfWork


async def resolve_patrol_binding(
        uow_factory: Callable[[], IUnitOfWork],
        mcp_pool: MCPConnectionPoolPort,
        session: Session,
        session_scope: OwnerScope,
        is_patrol: bool,
        patrol_run: Optional[PatrolRun],
        agent_config: AgentConfig,
        patrol_run_service_available: bool,
) -> tuple[Optional[str], AgentConfig]:
    """Validate and bind the Patrol session, returning
    (patrol_server_name, agent_config) — agent_config is returned back out
    because a bound Patrol run overrides max_run_seconds from the
    Pack-snapshot's validated defaults. A session claiming the ops-patrol
    skill must have exactly one persisted Run row bound to it (and vice
    versa); the Pack must still be active; the Run's Pack-owned Collector
    must still exist/be enabled/match scope; and its live capability hash
    must still match the hash captured at Pack-validation time (rejecting
    Collector capability drift) before any tool is exposed to the LLM.
    """
    patrol_server_name: Optional[str] = None
    if patrol_run is not None or is_patrol:
        if patrol_run is None or not is_patrol:
            raise NotFoundError("Patrol Session 缺少唯一的持久化 Run/Skill 绑定")
        if not patrol_run_service_available:
            raise NotFoundError("Patrol runtime service unavailable")
        async with uow_factory() as uow:
            scoped_run = await uow.patrol.get_run(patrol_run.id, session_scope)
            if scoped_run is None or scoped_run.session_id != session.id:
                raise NotFoundError("Patrol Run 不属于当前 Session OwnerScope")
            pack = await uow.patrol.get_pack(scoped_run.pack_id, session_scope)
            server_id = scoped_run.pack_snapshot.get("mcp_server_id")
            server = await uow.mcp_server.get_by_id(str(server_id), scope=session_scope) if server_id else None
        if pack is None or pack.status != PatrolPackStatus.ACTIVE:
            raise NotFoundError("Patrol Pack 非 active，运行时绑定已拒绝")
        if server is None or not server.enabled or server.id != pack.mcp_server_id:
            raise NotFoundError("Pack-owned Collector 不存在、已禁用或作用域不匹配")
        snapshot_hash = scoped_run.collector_capability_hash
        try:
            current_capabilities = await MCPPatrolCollectorValidator(
                mcp_pool
            ).get_capabilities(server)
        except Exception as exc:
            raise NotFoundError(
                f"Collector capability preflight failed: {str(exc)[:500]}"
            ) from exc
        current_hash = str(
            current_capabilities.get("overall_capability_hash") or ""
        )
        if not current_hash or current_hash != snapshot_hash:
            raise NotFoundError(
                "Collector capability drift detected; Pack 必须重新验证"
            )
        enabled_tools = set(scoped_run.pack_snapshot.get("enabled_tools") or [])
        allowed_source_tools = set(PATROL_PROBE_TOOLS) | {"get_capabilities"}
        if not enabled_tools or not enabled_tools.issubset(allowed_source_tools):
            raise NotFoundError("Patrol capability snapshot 包含未授权工具")
        configured_checks = PatrolPackConfig.model_validate(scoped_run.pack_snapshot["config"])
        agent_config = agent_config.model_copy(
            update={
                "max_run_seconds": configured_checks.defaults.run_timeout_seconds
            }
        )
        required_tools = {item.probe.tool for item in configured_checks.checks if item.enabled}
        if not required_tools.issubset(enabled_tools):
            raise NotFoundError("Patrol runtime tools 超出验证快照")
        patrol_server_name = server.name
    return patrol_server_name, agent_config
