#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Literal, List, Union, Optional, Any, Dict, Annotated

from pydantic import BaseModel, Field

from .event_policy import EVENT_SCHEMA_VERSION
from .file import File
from .plan import Plan, Step
from .search import SearchResultItem
from .tool_result import ToolResult


class EventVisibility(str, Enum):
    """Who may consume this event in the product UI."""
    USER = "user"
    INTERNAL = "internal"
    DEBUG = "debug"


class EventChannel(str, Enum):
    """Transport/rendering channel for an event."""
    UI = "ui"
    DEBUG = "debug"
    RUNTIME = "runtime"


class PlanEventStatus(str, Enum):
    """规划事件状态"""
    CREATED = "created"  # 已创建
    UPDATED = "updated"  # 已更新
    COMPLETED = "completed"  # 已完成


class StepEventStatus(str, Enum):
    """步骤事件状态"""
    STARTED = "started"  # 已开始
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败


class ToolEventStatus(str, Enum):
    """工具事件状态类型枚举"""
    CALLING = "calling"  # 调用中
    CALLED = "called"  # 调用完毕


class BaseEvent(BaseModel):
    """基础事件类型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 事件id
    type: Literal[""] = ""  # 事件的类型
    created_at: datetime = Field(default_factory=datetime.now)  # 事件创建时间
    schema_version: int = EVENT_SCHEMA_VERSION
    visibility: EventVisibility = EventVisibility.USER
    channel: EventChannel = EventChannel.UI
    persist: bool = True


class ClarifyOption(BaseModel):
    """澄清问题选项"""
    id: str
    label: str


class ClarifyQuestion(BaseModel):
    """澄清问题，支持单选、多选和自定义回答"""
    id: str
    prompt: str
    options: List[ClarifyOption] = Field(default_factory=list)
    allow_multiple: bool = False
    allow_custom: bool = True


class ClarifyEvent(BaseEvent):
    """澄清事件，展示交互式问题并等待用户回答"""
    type: Literal["clarify"] = "clarify"
    title: Optional[str] = None
    questions: List[ClarifyQuestion] = Field(default_factory=list)


class PlanEvent(BaseEvent):
    """规划事件类型"""
    type: Literal["plan"] = "plan"
    plan: Plan  # 规划
    status: PlanEventStatus = PlanEventStatus.CREATED  # 规划事件状态


class TitleEvent(BaseEvent):
    """标题事件类型"""
    type: Literal["title"] = "title"
    title: str = ""  # 标题


class StepEvent(BaseEvent):
    """子任务/步骤事件"""
    type: Literal["step"] = "step"
    step: Step  # 步骤信息
    status: StepEventStatus = StepEventStatus.STARTED
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None


class SubAgentEventStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


class SubAgentEvent(BaseEvent):
    """子 Agent 委派事件"""
    type: Literal["subagent"] = "subagent"
    subagent_id: str
    goal: str
    status: SubAgentEventStatus = SubAgentEventStatus.STARTED
    result_preview: Optional[str] = None
    error: Optional[str] = None


class MessageEvent(BaseEvent):
    """消息事件，包含人类消息和AI消息"""
    type: Literal["message"] = "message"
    role: Literal["user", "assistant"] = "assistant"  # 消息角色
    message: str = ""  # 消息本身
    attachments: List[File] = Field(default_factory=list)  # 附件列表信息
    stream_id: Optional[str] = None  # 流式消息合并 id


class MessageDeltaEvent(BaseEvent):
    """流式消息增量事件（实时 SSE，默认不落库）"""
    type: Literal["message_delta"] = "message_delta"
    stream_id: str
    role: Literal["user", "assistant"] = "assistant"
    delta: str = ""
    visibility: EventVisibility = EventVisibility.INTERNAL
    channel: EventChannel = EventChannel.RUNTIME
    persist: bool = False


class ReasoningDeltaEvent(BaseEvent):
    """流式思考内容增量事件（实时 SSE，默认不落库）"""
    type: Literal["reasoning_delta"] = "reasoning_delta"
    stream_id: str
    delta: str = ""
    visibility: EventVisibility = EventVisibility.DEBUG
    channel: EventChannel = EventChannel.DEBUG
    persist: bool = False


class ToolArgsDeltaEvent(BaseEvent):
    """流式工具参数增量事件（实时 SSE，默认不落库）"""
    type: Literal["tool_args_delta"] = "tool_args_delta"
    stream_id: str
    tool_call_id: str
    tool_name: str = ""
    delta: str = ""
    visibility: EventVisibility = EventVisibility.DEBUG
    channel: EventChannel = EventChannel.DEBUG
    persist: bool = False


class AssistantNoticeEvent(BaseEvent):
    """面向用户的简短助手提示，非结构化规划输出"""
    type: Literal["assistant_notice"] = "assistant_notice"
    message: str = ""
    i18n_key: Optional[str] = None
    i18n_params: Optional[Dict[str, str]] = None


class SessionStatusEvent(BaseEvent):
    """服务端权威的会话状态事件"""
    type: Literal["session_status"] = "session_status"
    status: Literal["pending", "running", "waiting", "completed", "cancelled", "failed"] = "running"


class DebugItemEvent(BaseEvent):
    """内部调试项，供调试面板展示，不进入普通聊天气泡"""
    type: Literal["debug_item"] = "debug_item"
    item_type: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)
    visibility: EventVisibility = EventVisibility.DEBUG
    channel: EventChannel = EventChannel.DEBUG


class BrowserToolContent(BaseModel):
    """浏览器工具扩展内容"""
    screenshot: str  # 浏览器快照截图


class SearchToolContent(BaseModel):
    """搜索工具内容"""
    results: List[SearchResultItem]  # 搜索结果列表


class ShellToolContent(BaseModel):
    """Shell工具内容"""
    console: Any  # 控制台内容


class FileToolContent(BaseModel):
    """文件工具内容"""
    content: str  # 文件内容


class MCPToolContent(BaseModel):
    """MCP工具内容"""
    result: Any  # MCP工具结果


class A2AToolContent(BaseModel):
    """A2A智能体工具内容"""
    a2a_result: Any  # A2A智能体调用结果


ToolContent = Union[
    BrowserToolContent,
    SearchToolContent,
    ShellToolContent,
    FileToolContent,
    MCPToolContent,
    A2AToolContent,
]


class ToolEvent(BaseEvent):
    """工具事件"""
    type: Literal["tool"] = "tool"
    tool_call_id: str  # 工具调用id
    tool_name: str  # 工具箱/工具集的名字
    tool_content: Optional[ToolContent] = None  # 工具扩展内容
    function_name: str  # LLM调用函数/工具名字
    function_args: Dict[str, Any]  # LLM生成的工具调用参数
    function_result: Optional[ToolResult] = None  # 工具调用结果
    status: ToolEventStatus = ToolEventStatus.CALLING  # 工具事件状态
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None


class WaitEvent(BaseEvent):
    """等待事件，等待用户输入确认"""
    type: Literal["wait"] = "wait"


class ErrorEvent(BaseEvent):
    """错误事件"""
    type: Literal["error"] = "error"
    error: str = ""  # 错误信息
    code: Optional[str] = None  # 分级错误码 MODEL_* / TOOL_* / ...
    parent_event_id: Optional[str] = None
    parent_span_id: Optional[str] = None


class DoneEvent(BaseEvent):
    """结束事件类型"""
    type: Literal["done"] = "done"


class UsageEvent(BaseEvent):
    """Token 用量事件，推送会话累计消耗。"""
    type: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    call_count: int = 0
    delta_prompt_tokens: int = 0
    delta_completion_tokens: int = 0


class ArtifactEvent(BaseEvent):
    """交付物事件"""
    type: Literal["artifact"] = "artifact"
    artifact_id: str
    kind: Literal["doc", "web"] = "doc"
    title: str = ""
    status: Literal["draft", "updated", "final"] = "draft"
    storage_ref: str = ""
    version: int = 1


class ApprovalEvent(BaseEvent):
    """人类审批门控事件"""
    type: Literal["approval"] = "approval"
    approval_id: str
    kind: Literal["plan", "tool", "takeover"] = "plan"
    payload: Dict[str, Any] = Field(default_factory=dict)
    options: List[str] = Field(default_factory=list)


# 定义应用事件类型声明
Event = Annotated[
    Union[
        ClarifyEvent,
        PlanEvent,
        TitleEvent,
        StepEvent,
        SubAgentEvent,
        MessageEvent,
        MessageDeltaEvent,
        ReasoningDeltaEvent,
        ToolArgsDeltaEvent,
        AssistantNoticeEvent,
        SessionStatusEvent,
        DebugItemEvent,
        ToolEvent,
        WaitEvent,
        ErrorEvent,
        UsageEvent,
        ArtifactEvent,
        ApprovalEvent,
        DoneEvent,
    ],
    Field(discriminator="type"),
]
