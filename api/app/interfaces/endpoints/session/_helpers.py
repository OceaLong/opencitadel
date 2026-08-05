#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""会话路由子包共享的辅助函数（跨审批/接管/CRUD 路由复用，不属于任何单一子路由）。"""
import logging
from typing import Optional

from fastapi import Request

from app.application.services.config_provider import get_runtime_config
from app.application.services.audit_service import AuditService
from app.application.services.llm_model_service import LLMModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.skill_service import SkillService
from app.domain.models.audit_log import AuditLog
from app.domain.models.event import BaseEvent
from app.domain.models.scope import OwnerScope, OwnerScopeType, WorkspaceContext
from app.domain.models.session import Session
from app.domain.utils.hitl import (
    PLAN_APPROVAL_PHASE,
    TAKEOVER_PHASE,
    TOOL_APPROVAL_PHASE,
    parse_gate_action,
)
from app.interfaces.client_ip import get_client_ip
from app.interfaces.endpoints.llm_model_routes import _to_response as llm_to_response
from app.interfaces.schemas.event import EventMapper
from app.interfaces.schemas.session import (
    ChatRequest,
    GetSessionResponse,
    ListSessionItem,
    TokenUsageSummaryResponse,
)
from app.interfaces.schemas.skill import SkillSummaryResponse

logger = logging.getLogger(__name__)


async def _record_gate_audit_if_needed(
        *,
        session: Session,
        message: str,
        ctx: WorkspaceContext,
        audit_service: AuditService,
        request: Request,
) -> None:
    if not session.pending_phase:
        return
    action, feedback = parse_gate_action(message or "")
    if action == "unknown":
        return
    meta = session.pending_metadata or {}
    pending = meta.get("pending_tool_call") or {}
    approval_batch_id = meta.get("approval_batch_id")
    audit_action = {
        TOOL_APPROVAL_PHASE: {
            "approve": "agent_tool_approve",
            "approve_same": "agent_tool_approve",
            "reject": "agent_tool_reject",
        },
        PLAN_APPROVAL_PHASE: {
            "approve": "agent_plan_approve",
            "approve_with_edits": "agent_plan_approve",
            "reject": "agent_plan_reject",
        },
        TAKEOVER_PHASE: {
            "takeover": "agent_takeover",
            "skip": "agent_takeover_skip",
        },
    }.get(session.pending_phase, {}).get(action)
    if not audit_action:
        return
    await audit_service.record(AuditLog(
        actor_user_id=ctx.principal.user_id,
        actor_ip=get_client_ip(request),
        action=audit_action,
        resource_type="session",
        resource_id=session.id,
        team_id=ctx.scope.team_id if ctx.scope.type == OwnerScopeType.TEAM else None,
        metadata={
            "decision": action,
            "feedback": feedback,
            "pending_phase": session.pending_phase,
            "approval_batch_id": approval_batch_id,
            "tool": pending.get("tool_name"),
            "args": pending.get("args"),
            "first_visit_domain": pending.get("first_visit_domain"),
            "operator_scope": session.operator_scope,
        },
    ))


def _format_clarify_answers(request: ChatRequest) -> Optional[str]:
    """Build the model-facing text summary for structured clarify answers."""
    if request.message:
        return request.message
    if not request.clarify_answers:
        return None
    lines = ["【澄清回复】"]
    for answer in request.clarify_answers:
        parts = list(answer.option_labels or [])
        custom = (answer.custom_text or "").strip()
        if custom:
            parts.append(f"自定义: {custom}")
        prompt = answer.prompt or answer.question_id
        lines.append(f"- {prompt}: {'；'.join(parts)}")
    return "\n".join(lines)


def _session_to_list_item(session: Session) -> ListSessionItem:
    return ListSessionItem(
        session_id=session.id,
        title=session.title,
        latest_message=session.latest_message,
        latest_message_at=session.latest_message_at,
        status=session.status,
        unread_message_count=session.unread_message_count,
        codebase_id=session.codebase_id,
        knowledge_base_id=session.knowledge_base_id,
        mode=session.mode,
        resource_bindings=session.resource_bindings,
    )


async def build_get_session_response(
        session: Session,
        llm_model_service: LLMModelService,
        skill_service: SkillService,
        scope: OwnerScope,
        token_usage_service: Optional[LLMTokenUsageService] = None,
        include_debug: bool = False,
        event_records: Optional[list[tuple[int, BaseEvent]]] = None,
        event_limit: int = 100,
) -> GetSessionResponse:
    """组装会话详情响应，避免在路由间直接调用 endpoint 函数"""
    model_resp = None
    skill_resp = None
    if session.model_id:
        try:
            model_resp = llm_to_response(
                await llm_model_service.get_model(session.model_id, scope=scope)
            )
        except Exception:
            pass
    if session.skill_id:
        summary = await skill_service.get_summary(session.skill_id, scope=scope)
        if summary:
            skill_resp = SkillSummaryResponse(**summary.model_dump())
    token_usage_resp = None
    if token_usage_service:
        try:
            model_prices = {}
            if session.model_id:
                try:
                    model = await llm_model_service.get_model(
                        session.model_id,
                        mask=False,
                        scope=scope,
                    )
                    model_prices[model.id] = (
                        model.input_price_per_million,
                        model.output_price_per_million,
                    )
                    model_prices[model.model_name] = (
                        model.input_price_per_million,
                        model.output_price_per_million,
                    )
                except Exception:
                    pass
            summary = await token_usage_service.get_session_summary(
                session.id,
                model_prices=model_prices or None,
            )
            token_usage_resp = TokenUsageSummaryResponse(
                prompt_tokens=summary.prompt_tokens,
                completion_tokens=summary.completion_tokens,
                total_tokens=summary.total_tokens,
                estimated_cost_usd=summary.estimated_cost_usd,
                call_count=summary.call_count,
            )
        except Exception as exc:
            logger.debug("获取会话 token 汇总失败: %s", exc)

    if event_records is None:
        events = session.events
        events_next_cursor = None
    else:
        events = [event for _, event in event_records]
        events_next_cursor = event_records[-1][0] if len(event_records) == event_limit else None

    return GetSessionResponse(
        session_id=session.id,
        title=session.title,
        status=session.status,
        events=EventMapper.events_to_sse_events(events, include_debug=include_debug),
        events_next_cursor=events_next_cursor,
        model_id=session.model_id,
        skill_id=session.skill_id,
        thinking_enabled=session.thinking_enabled,
        model=model_resp,
        skill=skill_resp,
        token_usage=token_usage_resp,
        operator_scope=session.operator_scope,
        operator_domains=session.operator_domains or [],
        gate_profile=session.gate_profile,
        awaiting_human=bool((session.pending_metadata or {}).get("awaiting_human")),
        codebase_id=session.codebase_id,
        knowledge_base_id=session.knowledge_base_id,
        mode=session.mode,
        resource_bindings=session.resource_bindings,
    )


# 流式获取会话详情睡眠间隔（config.yaml server.sessions_stream_interval_seconds）
SESSION_SLEEP_INTERVAL = max(5, get_runtime_config().server.sessions_stream_interval_seconds)
