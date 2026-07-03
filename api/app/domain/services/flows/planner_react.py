#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import AsyncGenerator, Optional, Callable, List
import asyncio
import uuid

from app.application.services.config_provider import get_runtime_config
from app.domain.external.browser import Browser
from app.domain.external.file_storage import FileStorage
from app.domain.external.json_parser import JSONParser
from app.domain.external.llm import LLM
from app.domain.external.sandbox import Sandbox
from app.domain.external.search import SearchEngine
from app.domain.models.app_config import AgentConfig
from app.domain.models.skill import Skill
from app.application.services.config_provider import get_runtime_config
from app.domain.models.event import (
    BaseEvent,
    ClarifyEvent,
    PlanEvent,
    PlanEventStatus,
    TitleEvent,
    AssistantNoticeEvent,
    DebugItemEvent,
    ApprovalEvent,
)
from app.domain.models.event import ErrorEvent, DoneEvent, WaitEvent
from app.domain.models.message import Message
from app.domain.models.plan import Plan, Step, ExecutionStatus
from app.domain.models.session import SessionStatus
from app.domain.services.agents.clarify import ClarifyAgent
from app.domain.services.agents.planner import PlannerAgent
from app.domain.services.agents.react import ReActAgent
from app.domain.services.tools.a2a import A2ATool
from app.domain.services.tools.base import BaseTool
from app.domain.services.tools.mcp import MCPTool
from app.domain.services.tools.subagent import SubAgentTool
from app.domain.services.tools.tool_registry import ToolRegistry
from app.domain.services.agent.sandbox_lifecycle import SandboxLifecycleCoordinator
from app.domain.models.event import StepEvent, StepEventStatus, MessageEvent
from app.domain.external.observability import ObservabilityPort
from app.domain.models.agent_runtime_settings import AgentRuntimeSettings
from .base import BaseFlow, FlowStatus
from ...repositories.uow import IUnitOfWork
from ...utils.hitl import (
    PLAN_APPROVAL_PHASE,
    derive_risk_tools_from_plan,
    merge_pending_metadata,
    parse_gate_action,
)

logger = logging.getLogger(__name__)

CLARIFY_PENDING_PHASE = "clarify"


class PlannerReActFlow(BaseFlow):
    """规划与执行流"""

    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],  # uow模块
            llm: LLM,  # 大语言模型
            agent_config: AgentConfig,  # 智能体配置
            session_id: str,  # 会话id
            json_parser: JSONParser,  # JSON解析器
            browser: Browser,  # 浏览器
            sandbox: Sandbox,  # 沙箱
            search_engine: SearchEngine,  # 搜索引擎
            mcp_tool: MCPTool,  # mcp工具
            a2a_tool: A2ATool,  # a2a远程agent
            observability_port: ObservabilityPort,
            runtime_settings: AgentRuntimeSettings,
            skill: Optional[Skill] = None,
            skill_prompt: str = "",
            long_term_memory_block: str = "",
            extra_tools: Optional[List[BaseTool]] = None,
            model_id: Optional[str] = None,
            file_storage: Optional[FileStorage] = None,
            stateful_tool_lock: Optional[asyncio.Lock] = None,
            sandbox_lifecycle: Optional["SandboxLifecycleCoordinator"] = None,
    ) -> None:
        """构造函数，完成规划与执行流的初始化"""
        self._stateful_tool_lock = stateful_tool_lock or asyncio.Lock()
        # 1.流初始化数据配置
        self._uow_factory = uow_factory
        self._session_id = session_id
        self.status = FlowStatus.IDLE
        self.plan: Optional[Plan] = None

        self._agent_config = agent_config
        self._flow_step_budget = agent_config.max_flow_steps
        self._flow_steps_used = 0
        self._observability = observability_port
        self._runtime_settings = runtime_settings

        tools = ToolRegistry.build_default_tools(
            sandbox=sandbox,
            browser=browser,
            search_engine=search_engine,
            llm=llm,
            mcp_tool=mcp_tool,
            a2a_tool=a2a_tool,
            extra_tools=extra_tools,
        )

        allowed_tool_names = skill.allowed_tools if (skill and skill.allowed_tools) else None
        self._subagent_tool: Optional[SubAgentTool] = None
        for tool in extra_tools or []:
            if isinstance(tool, SubAgentTool):
                self._subagent_tool = tool
                break

        # 3.创建澄清Agent（agent_params 已在 AgentService 合并）
        self.clarify = ClarifyAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            skill_prompt=skill_prompt,
            long_term_memory_block=long_term_memory_block,
            allowed_tool_names=allowed_tool_names,
            model_id=model_id,
            file_storage=file_storage,
            observability_port=self._observability,
            runtime_settings=self._runtime_settings,
            stateful_tool_lock=self._stateful_tool_lock,
        )
        logger.debug(f"创建澄清Agent成功, 会话id: {self._session_id}")

        # 4.创建规划Agent（agent_params 已在 AgentService 合并）
        self.planner = PlannerAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            skill_prompt=skill_prompt,
            long_term_memory_block=long_term_memory_block,
            allowed_tool_names=allowed_tool_names,
            model_id=model_id,
            file_storage=file_storage,
            observability_port=self._observability,
            runtime_settings=self._runtime_settings,
            stateful_tool_lock=self._stateful_tool_lock,
        )
        logger.debug(f"创建规划Agent成功, 会话id: {self._session_id}")

        # 5.创建执行Agent
        self.react = ReActAgent(
            uow_factory=uow_factory,
            session_id=session_id,
            agent_config=agent_config,
            llm=llm,
            json_parser=json_parser,
            tools=tools,
            skill_prompt=skill_prompt,
            long_term_memory_block=long_term_memory_block,
            allowed_tool_names=allowed_tool_names,
            model_id=model_id,
            file_storage=file_storage,
            observability_port=self._observability,
            runtime_settings=self._runtime_settings,
            stateful_tool_lock=self._stateful_tool_lock,
            sandbox_lifecycle=sandbox_lifecycle,
        )
        logger.debug(f"创建执行Agent成功, 会话id: {self._session_id}")

    async def _execute_parallel_steps(
            self,
            steps: List,
            message: Message,
    ) -> AsyncGenerator[BaseEvent, None]:
        """Execute a batch of parallelizable steps via isolated sub-agents."""
        if not self._subagent_tool:
            for step in steps:
                async for event in self.react.execute_step(self.plan, step, message):
                    yield event
            return

        async def _run_one(step):
            step.status = ExecutionStatus.RUNNING
            self.react.set_current_step(step.id)
            events = [StepEvent(step=step, status=StepEventStatus.STARTED)]
            result = await self._subagent_tool.run_step(step.description)
            payload = result.data if isinstance(result.data, dict) else {}
            subagent_id = payload.get("subagent_id") if isinstance(payload, dict) else None
            for aux in self._subagent_tool.drain_events(subagent_id=subagent_id):
                events.append(aux)
            if result.success:
                step.status = ExecutionStatus.COMPLETED
                step.success = True
                summary = str(payload.get("summary") or result.message or "")
                step.result = summary[:4000]
                events.append(StepEvent(step=step, status=StepEventStatus.COMPLETED))
                if step.result:
                    events.append(MessageEvent(role="assistant", message=step.result))
            else:
                step.status = ExecutionStatus.FAILED
                step.error = result.message
                events.append(StepEvent(step=step, status=StepEventStatus.FAILED))
            return events

        results = await asyncio.gather(*[_run_one(s) for s in steps])
        for event_list in results:
            for event in event_list:
                yield event

    @staticmethod
    def _build_parallel_update_step(steps: List[Step]) -> Step:
        """Build a synthetic completed step summarizing a parallel batch for re-planning."""
        summaries: List[str] = []
        success = True
        for item in steps:
            status = item.status.value if hasattr(item.status, "value") else str(item.status)
            if item.status != ExecutionStatus.COMPLETED or not item.success:
                success = False
            detail = item.result or item.error or ""
            summaries.append(f"- [{status}] {item.description}: {detail}".strip())
        return Step(
            id="parallel-batch",
            description="并行批次执行结果汇总",
            status=ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED,
            success=success,
            result="\n".join(summaries)[:4000],
        )

    async def invoke(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """传递消息，运行流，在六中调用planner&react智能体组合完成任务并返回对应事件"""
        tracer = self._observability.create_agent_tracer(self._session_id, "planner_react_flow")
        with tracer.span("planner_react_flow"):
            async for event in self._invoke_flow(message, tracer):
                yield event

    async def _invoke_flow(self, message: Message, tracer) -> AsyncGenerator[BaseEvent, None]:
        # 1.调用会话仓库查询会话是否存在
        async with self._uow_factory() as uow:
            session = await uow.session.get_by_id(self._session_id)
        if not session:
            raise ValueError(f"会话[{self._session_id}]不存在, 请核实后尝试")

        # 2.判断会话的状态是不是空闲
        #   如果不是则有可能有两种状态
        #    - 任务未结束，还在运行，但是用户又传递一条消息
        #    - Agent在等待人类输入，这时候人类输入了
        #   这时候均需要处理历史消息列表，避免AI(工具调用消息)后直接接上人类消息
        clarify_resume = session.pending_phase == CLARIFY_PENDING_PHASE
        plan_approval_resume = session.pending_phase == PLAN_APPROVAL_PHASE
        needs_event_history = session.status != SessionStatus.PENDING and not clarify_resume and not plan_approval_resume
        if needs_event_history:
            async with self._uow_factory() as uow:
                event_records = await uow.session.list_events(self._session_id, limit=200)
            if event_records:
                session.events = [event for _, event in event_records]

        if needs_event_history:
            logger.debug(f"会话[{self._session_id}]未处于空闲状态，回滚数据确保消息列表格式正确")
            await self.planner.roll_back(message)
            await self.react.roll_back(message)

        # 3.如果会话处于澄清等待或运行中收到新消息，则先进入澄清阶段再规划
        if clarify_resume:
            logger.debug(f"会话[{self._session_id}]处于澄清等待状态并传递了新消息")
            self.status = FlowStatus.CLARIFYING
        elif session.status == SessionStatus.RUNNING:
            logger.debug(f"会话[{self._session_id}]处于运行状态并传递了新消息")
            self.status = FlowStatus.CLARIFYING

        # 4.如果会话状态等于执行期等待人类输入，则恢复执行中
        if session.status == SessionStatus.WAITING and not clarify_resume and not plan_approval_resume:
            logger.debug(f"会话[{self._session_id}]处于等待状态并传递了新消息")
            self.status = FlowStatus.EXECUTING

        if plan_approval_resume:
            action, feedback = parse_gate_action(message.message)
            if action == "unknown":
                yield WaitEvent()
                return
            metadata = session.pending_metadata or {}
            if action in {"approve", "approve_with_edits"}:
                plan_data = metadata.get("edited_plan") if action == "approve_with_edits" else metadata.get("plan")
                if plan_data:
                    self.plan = Plan.model_validate(plan_data)
                await self._set_pending_phase(None)
                await self._set_pending_metadata(None)
                self.status = FlowStatus.EXECUTING
            else:
                await self._set_pending_phase(None)
                await self._set_pending_metadata(None)
                reject_msg = feedback or message.message
                message = Message(
                    message=reject_msg,
                    attachments=message.attachments,
                    vision_attachments=message.vision_attachments,
                )
                self.status = FlowStatus.CLARIFYING

        # 6.获取当前会话中最新事件（计划审批恢复时保留已批准计划）
        if not (plan_approval_resume and self.plan and self.status == FlowStatus.EXECUTING):
            self.plan = session.get_latest_plan()
        logger.info(f"Planner&ReAct流接收消息: {message.message[:50]}...")

        # 7.定义当前正在执行的子步骤
        step = None

        # 8.创建死循环执行任务，根据流的不同状态执行不同的操作
        while True:
            self._flow_steps_used += 1
            if self._flow_steps_used > self._flow_step_budget:
                logger.warning(
                    "Planner&ReAct流超过步骤预算 session=%s budget=%s",
                    self._session_id,
                    self._flow_step_budget,
                )
                yield ErrorEvent(error="任务步骤数超过上限，已终止执行")
                self.status = FlowStatus.COMPLETED
                break

            # 9.如果流的状态为空闲，则只需要将状态修改为规划中
            if self.status == FlowStatus.IDLE:
                logger.info(f"Planner&ReAct流状态从{FlowStatus.IDLE}变成{FlowStatus.CLARIFYING}")
                self.status = FlowStatus.CLARIFYING
            elif self.status == FlowStatus.CLARIFYING:
                logger.info("Planner&ReAct流开始澄清任务需求")
                self._observability.record_agent_step("clarify", "analyze")
                asked = False
                with tracer.span("clarify.analyze"):
                    async for event in self.clarify.analyze(message):
                        if isinstance(event, ClarifyEvent):
                            asked = True
                        yield event

                if asked:
                    await self._set_pending_phase(CLARIFY_PENDING_PHASE)
                    yield WaitEvent()
                    return

                await self._set_pending_phase(None)
                brief = self.clarify.last_brief or message.message
                message = Message(
                    message=brief,
                    attachments=message.attachments,
                    vision_attachments=message.vision_attachments,
                )
                logger.info(f"Planner&ReAct流状态从{FlowStatus.CLARIFYING}变成{FlowStatus.PLANNING}")
                self.status = FlowStatus.PLANNING
            elif self.status == FlowStatus.PLANNING:
                # 10.流状态为规划中，则调用规划Agent
                logger.info(f"Planner&ReAct流开始创建计划/Plan")
                self._observability.record_agent_step("planner", "create_plan")
                with tracer.span("planner.create_plan"):
                    async for event in self.planner.create_plan(message):
                        # 11.判断规划Agent是否返回规划事件
                        if isinstance(event, PlanEvent) and event.status == PlanEventStatus.CREATED:
                            # 12.创建计划成功时需要更新计划
                            self.plan = event.plan
                            logger.info(f"Planner&ReAct流成功创建计划, 共计: {len(event.plan.steps)} 步")

                            # 13.同步会话标题与安全提示，不展示 planner 结构化 JSON
                            yield TitleEvent(title=event.plan.title)
                            yield AssistantNoticeEvent(
                                message="我已制定计划，开始执行。",
                            )
                            yield DebugItemEvent(
                                item_type="planner_output",
                                payload=event.plan.model_dump(mode="json"),
                            )

                        # 14.将生成的事件直接输出(一般来说是PlanEvent)
                        yield event

                # 15.计划创建完成，更新流状态为执行中
                logger.info(f"压缩{self.planner.name} Agent记忆/上下文")
                await self.planner.summarize_and_compact()

                runtime = get_runtime_config()
                hitl = runtime.hitl
                if (
                    runtime.feature_flags.enable_hitl_gates
                    and hitl.plan_gate_enabled
                    and self.plan
                    and len(self.plan.steps) > 0
                ):
                    risk_tools = derive_risk_tools_from_plan(self.plan.steps, hitl.tool_gate_risk_list)
                    metadata = merge_pending_metadata(None, {
                        "plan": self.plan.model_dump(mode="json"),
                        "risk_tools": risk_tools,
                        "approved_tools": risk_tools if hitl.tool_gate_task_level_enabled else [],
                    })
                    await self._set_pending_metadata(metadata)
                    await self._set_pending_phase(PLAN_APPROVAL_PHASE)
                    yield ApprovalEvent(
                        approval_id=str(uuid.uuid4()),
                        kind="plan",
                        payload={
                            "plan": self.plan.model_dump(mode="json"),
                            "risk_tools": risk_tools,
                        },
                        options=["approve", "approve_with_edits", "reject"],
                    )
                    yield WaitEvent()
                    return

                logger.info(f"Planner&ReAct流状态从{FlowStatus.PLANNING}变成{FlowStatus.EXECUTING}")
                self.status = FlowStatus.EXECUTING

                # 16.判断计划是否生成，步骤是否正常
                if not self.plan or len(self.plan.steps) == 0:
                    logger.info(f"Planner&ReAct流创建计划失败或无子步骤")
                    self.status = FlowStatus.COMPLETED
                    break
            elif self.status == FlowStatus.EXECUTING:
                self.plan.status = ExecutionStatus.RUNNING

                parallel_enabled = get_runtime_config().feature_flags.enable_parallel_step_execution
                batch = self.plan.get_next_parallel_batch() if parallel_enabled else []
                if parallel_enabled and self._subagent_tool and len(batch) > 1 and all(s.parallelizable for s in batch):
                    logger.info(
                        "Planner&ReAct流并行执行 %s 个步骤 session=%s",
                        len(batch),
                        self._session_id,
                    )
                    async for event in self._execute_parallel_steps(batch, message):
                        yield event
                    step = self._build_parallel_update_step(batch)
                    self.status = FlowStatus.UPDATING
                    continue

                step = self.plan.get_next_step()

                # 19.如果不存在下一个需要执行的自己花，则更新流状态并执行后续步骤
                if not step:
                    logger.info(f"Planner&ReAct流状态从{FlowStatus.EXECUTING}变成{FlowStatus.SUMMARIZING}")
                    self.status = FlowStatus.SUMMARIZING
                    continue

                # 20.调用执行Agent执行对应的步骤
                logger.info(f"Planner&ReAct流开始执行步骤 {step.id}: {step.description[:50]}...")
                async for event in self.react.execute_step(
                        self.plan,
                        step,
                        message,
                        vision_attachments=message.vision_attachments,
                ):
                    yield event

                # 21.压缩执行Agent记忆，避免上下文腐化+消耗大量token
                logger.info(f"压缩{self.react.name} Agent记忆/上下文")
                await self.react.summarize_and_compact()

                # 22.将状态更新为updating
                self.status = FlowStatus.UPDATING
            elif self.status == FlowStatus.UPDATING:
                # 23.流状态为更新表示需要更新计划
                if not self.plan.get_next_step():
                    logger.info("Planner&ReAct流无剩余步骤，跳过计划更新")
                    self.status = FlowStatus.SUMMARIZING
                    continue
                logger.info(f"Planner&ReAct流开始更新计划")
                async for event in self.planner.update_plan(self.plan, step):
                    yield event
                logger.info(f"压缩{self.planner.name} Agent记忆/上下文")
                await self.planner.summarize_and_compact()

                # 24.计划更新完成，需要执行相应的子步骤
                logger.info(f"Planner&ReAct流状态从{FlowStatus.UPDATING}变成{FlowStatus.EXECUTING}")
                self.status = FlowStatus.EXECUTING
            elif self.status == FlowStatus.SUMMARIZING:
                # 25.流状态为总结中，则意味着所有子步骤都执行完成
                logger.info(f"Planner&ReAct流开始总结")
                async for event in self.react.summarize(message):
                    yield event

                # 26.总结完毕，意味着流即将结束
                logger.info(f"Planner&ReAct流状态从{FlowStatus.SUMMARIZING}变成{FlowStatus.COMPLETED}")
                self.status = FlowStatus.COMPLETED
            elif self.status == FlowStatus.COMPLETED:
                if self.plan is None:
                    self.status = FlowStatus.IDLE
                    break
                # 27.计划状态已完成则更新plan状态，并发送计划事件通知API已完成
                self.plan.status = ExecutionStatus.COMPLETED
                self.status = FlowStatus.IDLE
                yield PlanEvent(status=PlanEventStatus.COMPLETED, plan=self.plan)
                break
        # 28.任务已经结束则返回结束事件
        yield DoneEvent()
        logger.info(f"Planner&ReAct流处理任务消息已完毕")

    @property
    def done(self) -> bool:
        """只读属性，返回流是否运行结束"""
        return self.status == FlowStatus.IDLE

    async def _set_pending_phase(self, phase: Optional[str]) -> None:
        """持久化等待恢复阶段，用于区分澄清等待和执行期等待。"""
        async with self._uow_factory() as uow:
            await uow.session.set_pending_phase(self._session_id, phase)

    async def _set_pending_metadata(self, metadata: Optional[dict]) -> None:
        async with self._uow_factory() as uow:
            await uow.session.set_pending_metadata(self._session_id, metadata)
