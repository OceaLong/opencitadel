#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import uuid
from typing import AsyncGenerator, Optional, List

from datetime import datetime

from app.application.services.config_provider import get_runtime_config
from app.domain.models.event import (
    StepEventStatus,
    StepEvent,
    ToolEvent,
    MessageEvent,
    ErrorEvent,
    ToolEventStatus,
    WaitEvent,
    ApprovalEvent,
    BaseEvent,
)
from app.domain.models.tool_result import ToolResult
from app.domain.utils.hitl import TAKEOVER_PHASE, TOOL_APPROVAL_PHASE, merge_pending_metadata, parse_gate_action
from app.domain.models.file import File
from app.domain.models.message import Message, VisionAttachment
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.services.agents.structured_parse import StructuredParseError, parse_structured_output
from app.domain.services.prompts.loader import compose_system_prompt, detect_locale_from_text, load_prompts
from .base import BaseAgent

logger = logging.getLogger(__name__)

_PLAN_SNAPSHOT_MAX_STEPS = 30


def render_plan_snapshot(plan: Plan, current_step_id: str) -> str:
    """Render a compact todo-style plan snapshot for context recitation."""
    lines: List[str] = []
    steps = plan.steps[:_PLAN_SNAPSHOT_MAX_STEPS]
    for step in steps:
        if step.status == ExecutionStatus.COMPLETED:
            mark = "x"
        elif step.id == current_step_id or step.status == ExecutionStatus.RUNNING:
            mark = ">"
        else:
            mark = " "
        lines.append(f"- [{mark}] {step.description}")
    if len(plan.steps) > _PLAN_SNAPSHOT_MAX_STEPS:
        lines.append(f"- ... (仅显示前 {_PLAN_SNAPSHOT_MAX_STEPS}/{len(plan.steps)} 步)")
    return "\n".join(lines) if lines else "(无计划步骤)"


class ReActAgent(BaseAgent):
    """基于ReAct架构的执行Agent"""
    name: str = "react"
    _format: str = "json_object"  # format控制的是content、工具调用控制的是tool_calls两者不冲突

    def _should_emit_deltas(self) -> bool:
        return type(self) is ReActAgent

    async def execute_step(
            self,
            plan: Plan,
            step: Step,
            message: Message,
            vision_attachments=None,
    ) -> AsyncGenerator[BaseEvent, None]:
        """根据传递的消息+规划+子步骤，执行相应的子步骤"""
        prompts = load_prompts(plan.language)
        saved_prompt = self._system_prompt
        self._system_prompt = compose_system_prompt(prompts, prompts.react.REACT_SYSTEM_PROMPT)
        # 1.根据传递的内容生成执行消息
        query = prompts.react.EXECUTION_PROMPT.format(
            message=message.message,
            attachments="\n".join(message.attachments),
            language=plan.language,
            step=step.description,
            plan_snapshot=render_plan_snapshot(plan, step.id),
        )

        # 2.更新步骤的执行状态为运行中并返回Step事件
        step.status = ExecutionStatus.RUNNING
        self.set_current_step(step.id)
        yield StepEvent(step=step, status=StepEventStatus.STARTED)

        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)

        if session and session.pending_phase == TAKEOVER_PHASE:
            action, _ = parse_gate_action(message.message)
            if action == "unknown":
                yield WaitEvent()
                return
            async with self._uow_factory() as uow:
                await uow.session.set_pending_phase(self._session_id, None)
                await uow.session.set_pending_metadata(self._session_id, None)
            await self.roll_back(message)
            step_failed = False
            try:
                async for event in self.continue_tool_iteration_loop(emit_deltas=self._should_emit_deltas()):
                    stop_step = False
                    async for out in self._handle_execute_event(event, step):
                        if isinstance(out, tuple) and out[0] == "failed":
                            step_failed = True
                            yield StepEvent(step=step, status=StepEventStatus.FAILED)
                        elif isinstance(out, WaitEvent):
                            stop_step = True
                            yield out
                        else:
                            yield out
                    if stop_step:
                        return
            finally:
                self._system_prompt = saved_prompt
            if not step_failed:
                step.status = ExecutionStatus.COMPLETED
            return

        if session and session.pending_phase == TOOL_APPROVAL_PHASE:
            try:
                async for ev in self._resume_tool_approval(session, message, step):
                    yield ev
                if not step.error and step.status != ExecutionStatus.FAILED:
                    step.status = ExecutionStatus.COMPLETED
            finally:
                self._system_prompt = saved_prompt
            return

        step_failed = False
        try:
            async for event in self.invoke(
                query,
                vision_attachments=vision_attachments,
                emit_deltas=self._should_emit_deltas(),
            ):
                stop_step = False
                async for out in self._handle_execute_event(event, step):
                    if isinstance(out, tuple) and out[0] == "failed":
                        step_failed = True
                        yield StepEvent(step=step, status=StepEventStatus.FAILED)
                    elif isinstance(out, WaitEvent):
                        stop_step = True
                        yield out
                    else:
                        yield out
                if stop_step:
                    return
        finally:
            self._system_prompt = saved_prompt

        if not step_failed:
            step.status = ExecutionStatus.COMPLETED

    async def _handle_execute_event(self, event: BaseEvent, step: Step) -> AsyncGenerator[BaseEvent | tuple, None]:
        if isinstance(event, ToolEvent):
            if event.function_name == "message_ask_user":
                if event.status == ToolEventStatus.CALLING:
                    yield MessageEvent(
                        role="assistant",
                        message=event.function_args.get("text", "")
                    )
                elif event.status == ToolEventStatus.CALLED:
                    takeover = (event.function_args or {}).get("suggest_user_takeover")
                    if takeover == "browser":
                        runtime = get_runtime_config()
                        async with self._uow_factory() as uow:
                            current = await uow.session.get_by_id(self._session_id)
                            await uow.session.set_pending_phase(self._session_id, TAKEOVER_PHASE)
                            await uow.session.set_pending_metadata(
                                self._session_id,
                                merge_pending_metadata(
                                    current.pending_metadata if current else None,
                                    {
                                        "takeover": {
                                            "started_at": datetime.now().isoformat(),
                                            "timeout_minutes": runtime.hitl.takeover_timeout_minutes,
                                        },
                                    },
                                ),
                            )
                        yield ApprovalEvent(
                            approval_id=str(uuid.uuid4()),
                            kind="takeover",
                            payload={"mode": "browser"},
                            options=["takeover", "skip"],
                        )
                    yield WaitEvent()
                    return
                return
            yield event
            return
        if isinstance(event, MessageEvent):
            step.status = ExecutionStatus.COMPLETED
            new_step = await parse_structured_output(
                event.message,
                Step,
                self._json_parser,
                retry_budget=getattr(self, "_retry_budget", None),
            )
            step.success = new_step.success
            step.result = new_step.result
            step.attachments = new_step.attachments
            yield StepEvent(step=step, status=StepEventStatus.COMPLETED)
            if step.result:
                yield MessageEvent(role="assistant", message=step.result)
            return
        if isinstance(event, ErrorEvent):
            step.status = ExecutionStatus.FAILED
            step.error = event.error
            yield ("failed",)
            return
        if isinstance(event, WaitEvent):
            yield event
            return
        yield event

    async def _resume_tool_approval(self, session, message: Message, step: Step):
        meta = session.pending_metadata or {}
        pending = meta.get("pending_tool_call") or {}
        action, feedback = parse_gate_action(message.message)
        tool_name = pending.get("tool_name", "")
        tool_args = pending.get("args") or {}
        tool_call_id = pending.get("tool_call_id") or str(uuid.uuid4())

        if action == "unknown":
            yield WaitEvent()
            return

        if action == "approve_same":
            approved = list(meta.get("approved_tools") or [])
            if tool_name and tool_name not in approved:
                approved.append(tool_name)
            meta = merge_pending_metadata(meta, {"approved_tools": approved})
            async with self._uow_factory() as uow:
                await uow.session.set_pending_phase(self._session_id, None)
                await uow.session.set_pending_metadata(self._session_id, meta)
        else:
            async with self._uow_factory() as uow:
                await uow.session.set_pending_phase(self._session_id, None)
                await uow.session.set_pending_metadata(self._session_id, None)

        if action == "reject":
            tool_messages = [{
                "role": "tool",
                "tool_call_id": tool_call_id,
                "_function_name": tool_name,
                "content": ToolResult(
                    success=False,
                    message=feedback or "用户拒绝了此操作",
                ).model_dump_json(),
            }]
            async for event in self.continue_tool_iteration_loop(
                    inject_tool_messages=tool_messages,
                    emit_deltas=self._should_emit_deltas(),
            ):
                async for out in self._handle_execute_event(event, step):
                    yield out
            return

        if action not in {"approve", "approve_same"}:
            yield WaitEvent()
            return

        try:
            tool = self._resolve_tool(tool_name)
            result = await self._invoke_tool(tool, tool_name, tool_args)
            tool_label = tool.name
        except Exception as exc:
            result = ToolResult(success=False, message=str(exc))
            tool_label = "tool"
        yield ToolEvent(
            tool_call_id=tool_call_id,
            tool_name=tool_label,
            function_name=tool_name,
            function_args=tool_args,
            function_result=result,
            status=ToolEventStatus.CALLED,
        )
        tool_messages = [{
            "role": "tool",
            "tool_call_id": tool_call_id,
            "_function_name": tool_name,
            "content": result.model_dump_json(),
        }]
        async for event in self.continue_tool_iteration_loop(
                inject_tool_messages=tool_messages,
                emit_deltas=self._should_emit_deltas(),
        ):
            async for out in self._handle_execute_event(event, step):
                yield out

    async def summarize(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """调用Agent汇总历史的消息并生成最终回复+附件"""
        self.set_current_step("summarize")
        prompts = load_prompts(detect_locale_from_text(message.message))
        saved_prompt = self._system_prompt
        self._system_prompt = compose_system_prompt(prompts, prompts.react.REACT_SYSTEM_PROMPT)
        # 1.构建请求query
        query = prompts.react.SUMMARIZE_PROMPT

        max_repair_attempts = 2
        current_query = query
        try:
            for attempt in range(max_repair_attempts + 1):
                # 2.调用invoke方法获取Agent生成的事件（汇总阶段不再重传用户图片）
                async for event in self.invoke(current_query, emit_deltas=self._should_emit_deltas()):
                    # 3.判断事件类型是否为消息事件，如果是则表示Agent结构化生成汇总内容
                    if isinstance(event, MessageEvent):
                        # 4.记录日志并解析输出内容
                        logger.info(f"执行Agent生成汇总内容: {event.message}")
                        try:
                            # 5.将解析数据转换为Message对象
                            summary_message = await parse_structured_output(
                                event.message,
                                Message,
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

                        # 6.提取消息中的附件信息
                        attachments = [File(filepath=filepath) for filepath in summary_message.attachments]

                        # 7.返回消息事件并将消息+附件进行相应
                        yield MessageEvent(
                            role="assistant",
                            message=summary_message.message,
                            attachments=attachments,
                        )
                        return
                    else:
                        # 8.其他事件则直接返回
                        yield event
        finally:
            self._system_prompt = saved_prompt
