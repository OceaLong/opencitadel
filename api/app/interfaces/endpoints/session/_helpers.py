"""会话路由子包共享的辅助函数（跨审批/接管/CRUD 路由复用，不属于任何单一子路由）。"""

import contextlib
import logging

from app.application.execution.public_projection import PublicEventPage
from app.application.services.inference_model_service import InferenceModelService
from app.application.services.llm_token_usage_service import LLMTokenUsageService
from app.application.services.runtime_policy_reader import OperationsPolicyReader
from app.application.services.skill_service import SkillService
from app.domain.models.scope import OwnerScope
from app.domain.models.session import Session
from app.domain.utils.time_utils import utc_now
from app.interfaces.schemas.inference import InferenceModelResponse
from app.interfaces.schemas.session import (
    ExecutionEventResponse,
    GetSessionResponse,
    ListSessionItem,
    TokenUsageSummaryResponse,
)
from app.interfaces.schemas.skill import SkillSummaryResponse

logger = logging.getLogger(__name__)


def _session_to_list_item(session: Session) -> ListSessionItem:
    return ListSessionItem(
        session_id=session.id,
        title=session.title,
        latest_message=session.latest_message,
        latest_message_at=session.latest_message_at,
        status=session.status,
        unread_message_count=session.unread_message_count,
        mode=session.mode,
        resource_bindings=session.resource_bindings,
    )


async def build_get_session_response(
    session: Session,
    inference_model_service: InferenceModelService,
    skill_service: SkillService,
    scope: OwnerScope,
    token_usage_service: LLMTokenUsageService | None = None,
    event_page: PublicEventPage | None = None,
) -> GetSessionResponse:
    """组装会话详情响应，避免在路由间直接调用 endpoint 函数"""
    model_resp = None
    skill_resp = None
    if session.model_id:
        with contextlib.suppress(Exception):
            model_resp = InferenceModelResponse.from_domain(
                await inference_model_service.get_model(session.model_id, scope=scope)
            )
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
                    model = await inference_model_service.get_model(
                        session.model_id,
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
                except (OSError, RuntimeError, ValueError):
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
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("获取会话 token 汇总失败: %s", exc)

    events = (
        []
        if event_page is None
        else [
            ExecutionEventResponse(**event.model_dump(mode="python")) for event in event_page.events
        ]
    )

    return GetSessionResponse(
        session_id=session.id,
        title=session.title,
        status=session.status,
        events=events,
        events_next_cursor=event_page.next_cursor if event_page else None,
        model_id=session.model_id,
        skill_id=session.skill_id,
        thinking_enabled=session.thinking_enabled,
        model=model_resp,
        skill=skill_resp,
        token_usage=token_usage_resp,
        operator_scope=session.operator_scope,
        operator_domains=session.operator_domains or [],
        mode=session.mode,
        resource_bindings=session.resource_bindings,
    )


async def session_stream_interval_seconds(policy_reader: OperationsPolicyReader) -> int:
    active = await policy_reader.active_operations(require_fresh=True, now=utc_now())
    return active.revision.policy.traffic.session_stream_interval_seconds
