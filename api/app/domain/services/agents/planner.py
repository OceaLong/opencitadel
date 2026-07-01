#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import json
from typing import Optional, AsyncGenerator

from app.domain.models.event import BaseEvent, MessageEvent, PlanEvent, PlanEventStatus
from app.domain.models.message import Message
from app.domain.models.plan import Plan, Step
from app.domain.schemas.planner_output import PlannerPlanSchema, PlannerUpdateSchema
from app.domain.services.prompts.planner import (
    PLANNER_SYSTEM_PROMPT,
    CREATE_PLAN_PROMPT,
    UPDATE_PLAN_PROMPT,
)
from app.domain.services.prompts.system import SYSTEM_PROMPT
from app.domain.services.agents.structured_parse import StructuredParseError, parse_structured_output
from .base import BaseAgent

"""
多Agent系统/flow=PlannerAgent+ReActAgent

顺序:
1. PlannerAgent生成规划;
2. 循环取出规划中的子步骤，让ReActAgent执行，依次迭代;
3. ReActAgent执行完每一个子步骤之后，需要将子步骤结果+Plan传递给PlannerAgent让其更新计划/Plan；
4. 循环取出规划中的子步骤，让ReActAgent执行，依次迭代;
5. ...
6. 直到所有子任务/步骤都完成，这时候将子步骤的所有结果汇总进行总结(ReActAgent);

PlannerAgent:
- 功能: 将用户的需求拆解成多个子任务+根据已完成的子任务更新规划
- 提示词: 创建规划的prompt、更新规划的prompt

ReActAgent:
- 功能: 迭代执行完每一个子任务、汇总所有的子任务进行总结
- 提示词: 执行任务的prompt、汇总总结prompt
"""

logger = logging.getLogger(__name__)


class PlannerAgent(BaseAgent):
    """规划Agent，用于将用户的任务/需求拆解成多个子步骤"""
    name: str = "planner"
    _system_prompt: str = SYSTEM_PROMPT + PLANNER_SYSTEM_PROMPT
    _format: Optional[str] = "json_object"
    _tool_choice: Optional[str] = "none"

    async def create_plan(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """根据用户传递的消息创建计划/规划，迭代返回对应的事件"""
        self.set_current_step("create_plan")
        # 1.根据用户传递的消息生成创建plan的提示词
        query = CREATE_PLAN_PROMPT.format(
            message=message.message,
            attachments="\n".join(message.attachments),
        )

        max_repair_attempts = 2
        current_query = query
        for attempt in range(max_repair_attempts + 1):
            async for event in self.invoke(
                current_query,
                vision_attachments=message.vision_attachments if attempt == 0 else None,
                emit_deltas=False,
                response_schema=PlannerPlanSchema,
            ):
                if isinstance(event, MessageEvent):
                    logger.info(f"PlannerAgent生成消息: {event.message}")
                    try:
                        validated = await parse_structured_output(
                            event.message,
                            PlannerPlanSchema,
                            self._json_parser,
                            retry_budget=getattr(self, "_retry_budget", None),
                        )
                    except StructuredParseError as exc:
                        if attempt >= max_repair_attempts:
                            raise
                        current_query = (
                            f"{query}\n\n上次输出不符合结构化 schema，请修正后只返回 JSON。\n"
                            f"校验错误:\n{exc}"
                        )
                        break
                    plan = Plan.model_validate(validated.model_dump())
                    yield PlanEvent(plan=plan, status=PlanEventStatus.CREATED)
                    return
                else:
                    yield event

    async def update_plan(self, plan: Plan, step: Step) -> AsyncGenerator[BaseEvent, None]:
        """根据传递的原始规划+子步骤更新事件"""
        self.set_current_step(f"update_plan:{step.id}")
        # 1.使用plan+step创建更新Plan提示词
        pending_plan_payload = plan.model_dump(mode="json")
        pending_plan_payload["steps"] = [
            pending_step.model_dump(mode="json")
            for pending_step in plan.steps
            if not pending_step.done
        ]
        query = UPDATE_PLAN_PROMPT.format(
            plan=json.dumps(pending_plan_payload, ensure_ascii=False),
            step=step.model_dump_json(),
        )

        max_repair_attempts = 2
        current_query = query
        for attempt in range(max_repair_attempts + 1):
            async for event in self.invoke(
                current_query,
                emit_deltas=False,
                response_schema=PlannerUpdateSchema,
            ):
                if isinstance(event, MessageEvent):
                    logger.info(f"PlannerAgent生成消息: {event.message}")
                    try:
                        validated = await parse_structured_output(
                            event.message,
                            PlannerUpdateSchema,
                            self._json_parser,
                            retry_budget=getattr(self, "_retry_budget", None),
                        )
                    except StructuredParseError as exc:
                        if attempt >= max_repair_attempts:
                            raise
                        current_query = (
                            f"{query}\n\n上次输出不符合结构化 schema，请修正后只返回 JSON。\n"
                            f"校验错误:\n{exc}"
                        )
                        break

                    # 6.拷贝更新计划中的steps，避免造成数据污染
                    new_steps = [Step.model_validate(step.model_dump()) for step in validated.steps]

                    # 7.查询旧计划中第一个未完成的计划
                    first_pending_index = None
                    for idx, step in enumerate(plan.steps):
                        if not step.done:
                            first_pending_index = idx
                            break

                    # 8.判断是否有未完成的步骤，如果有则执行更新
                    if first_pending_index is not None:
                        # 9.获取历史已完成的子步骤并更新
                        updated_steps = plan.steps[:first_pending_index]
                        updated_steps.extend(new_steps)

                        # 10.更新plan规划
                        plan.steps = updated_steps

                    # 11.返回规划更新事件
                    yield PlanEvent(plan=plan, status=PlanEventStatus.UPDATED)
                    return
                else:
                    # 其他事件则直接返回
                    yield event
