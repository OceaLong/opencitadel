#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import AsyncGenerator, Dict, Any, List, Optional

from app.domain.models.event import (
    BaseEvent,
    ClarifyEvent,
    ClarifyQuestion,
    MessageEvent,
)
from app.domain.models.message import Message
from app.domain.schemas.clarify_output import ClarifyOutputSchema
from app.domain.services.agents.structured_parse import StructuredParseError, parse_structured_output
from app.domain.services.prompts.loader import compose_clarify_system_prompt, detect_locale_from_text, load_prompts
from app.domain.utils.prompt_context import format_user_attachments_for_prompt
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ClarifyAgent(BaseAgent):
    """澄清Agent，用于在规划前判断是否需要补充关键信息。"""
    name: str = "clarify"
    _format: Optional[str] = "json_object"
    _tool_choice: Optional[str] = "none"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.last_brief: Optional[str] = None

    def _get_available_tools(self) -> List[Dict[str, Any]]:
        """澄清阶段不使用工具，确保模型按 JSON response_format 输出。"""
        return []

    async def analyze(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """分析用户消息，返回澄清问题或记录可用于规划的完整摘要。"""
        self.set_current_step("analyze")
        self._refresh_retry_budget()
        self.last_brief = None
        prompts = load_prompts(detect_locale_from_text(message.message))
        self.set_locale(prompts.locale)
        saved_prompt = self._system_prompt
        self._system_prompt = compose_clarify_system_prompt(
            prompts,
            skill_prompt=self._skill_prompt,
            long_term_memory_block=self._long_term_memory_block,
        )
        query = prompts.clarify.CLARIFY_PROMPT.format(
            message=message.message,
            attachments=format_user_attachments_for_prompt(message, locale=prompts.locale),
        )

        max_repair_attempts = 2
        current_query = query
        try:
            for attempt in range(max_repair_attempts + 1):
                async for event in self.invoke(
                    current_query,
                    vision_attachments=message.vision_attachments if attempt == 0 else None,
                    emit_deltas=False,
                    response_schema=ClarifyOutputSchema,
                ):
                    if isinstance(event, MessageEvent):
                        logger.info("ClarifyAgent生成消息: %s", event.message)
                        try:
                            validated = await parse_structured_output(
                                event.message,
                                ClarifyOutputSchema,
                                self._json_parser,
                            )
                        except StructuredParseError as exc:
                            if attempt >= max_repair_attempts:
                                raise
                            budget = getattr(self, "_retry_budget", None)
                            if budget is not None:
                                budget.consume("structured_validation_retry", ignore_deadline=True)
                            current_query = (
                                f"{query}\n\n"
                                f"{prompts.internal.STRUCTURED_REPAIR_HINT.format(errors=exc)}"
                            )
                            break

                        logger.info(
                            "ClarifyAgent判定: needs_clarification=%s questions=%s brief_len=%s",
                            validated.needs_clarification,
                            len(validated.questions),
                            len(validated.brief or ""),
                        )
                        if validated.needs_clarification:
                            questions = [
                                ClarifyQuestion.model_validate(question.model_dump())
                                for question in validated.questions
                            ]
                            yield ClarifyEvent(
                                title=validated.title,
                                questions=questions,
                            )
                        else:
                            self.last_brief = validated.brief
                        return

                    yield event
        finally:
            self._system_prompt = saved_prompt
