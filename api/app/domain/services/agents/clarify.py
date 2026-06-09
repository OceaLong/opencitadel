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
from app.domain.services.prompts.clarify import CLARIFY_PROMPT, CLARIFY_SYSTEM_PROMPT
from app.domain.services.prompts.system import SYSTEM_PROMPT
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ClarifyAgent(BaseAgent):
    """澄清Agent，用于在规划前判断是否需要补充关键信息。"""
    name: str = "clarify"
    _system_prompt: str = SYSTEM_PROMPT + CLARIFY_SYSTEM_PROMPT
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
        self.last_brief = None
        query = CLARIFY_PROMPT.format(
            message=message.message,
            attachments="\n".join(message.attachments),
        )

        async for event in self.invoke(
            query,
            vision_attachments=message.vision_attachments,
            emit_deltas=False,
        ):
            if isinstance(event, MessageEvent):
                logger.info("ClarifyAgent生成消息: %s", event.message)
                parsed_obj = await self._json_parser.invoke(event.message)
                validated = ClarifyOutputSchema.model_validate(parsed_obj)

                if validated.needs_clarification and validated.questions:
                    questions = [
                        ClarifyQuestion.model_validate(question.model_dump())
                        for question in validated.questions
                    ]
                    yield ClarifyEvent(
                        title=validated.title,
                        questions=questions,
                    )
                else:
                    self.last_brief = (validated.brief or message.message).strip()
                continue

            yield event
