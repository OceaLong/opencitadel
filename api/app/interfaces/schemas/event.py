#!/usr/bin/env python
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, Self, Type, Literal, List, Union, get_args

from pydantic import BaseModel, Field, ConfigDict

from app.domain.models.event import (
    Event,
    PlanEvent,
    ToolEventStatus,
    ToolEvent,
    StepEvent,
)
from app.domain.models.event_policy import EVENT_SCHEMA_VERSION, project_events
from app.domain.models.file import File
from app.domain.models.plan import ExecutionStatus


class BaseEventData(BaseModel):
    """基础事件数据"""
    event_id: Optional[str] = None  # 事件id
    created_at: datetime = Field(default_factory=datetime.now)  # 事件时间
    schema_version: int = EVENT_SCHEMA_VERSION
    visibility: Literal["user", "internal", "debug"] = "user"
    channel: Literal["ui", "debug", "runtime"] = "ui"
    persist: bool = True

    # pydantic v2写法，序列化时将datetime转换为时间戳
    model_config = ConfigDict(json_encoders={
        datetime: lambda v: int(v.timestamp())
    })

    @classmethod
    def base_event_data(cls, event: Event) -> Dict[str, Any]:
        """类方法，用于将事件Domain模型转换成基础事件数据字典"""
        visibility = getattr(event, "visibility", "user")
        channel = getattr(event, "channel", "ui")
        if hasattr(visibility, "value"):
            visibility = visibility.value
        if hasattr(channel, "value"):
            channel = channel.value
        return {
            "event_id": event.id,
            "created_at": int(event.created_at.timestamp()),
            "schema_version": getattr(event, "schema_version", 1),
            "visibility": visibility,
            "channel": channel,
            "persist": getattr(event, "persist", True),
        }

    @classmethod
    def from_event(cls, event: Event) -> Self:
        """从事件Domain模型中构建基础事件数据"""
        return cls(
            **cls.base_event_data(event),
            **EventMapper.event_payload_data(event),
        )


class BaseSSEEvent(BaseModel):
    """基础流式事件数据类型"""
    event: str  # 事件类型
    data: BaseEventData  # 数据

    @classmethod
    def from_event(cls, event: Event) -> Self:
        """将事件Domain模型转换成基础流式事件"""
        # 1.获取事件数据的类型，如果没有则使用基础事件数据BaseEventData
        data_class: Type[BaseEventData] = cls.__annotations__.get("data", BaseEventData)

        # 2.调用构造函数完成初始化
        return cls(
            event=event.type,
            data=data_class.from_event(event),
        )


class CommonEventData(BaseEventData):
    """通用事件数据，让结构允许填充额外的数据"""
    model_config = ConfigDict(
        json_encoders={
            datetime: lambda v: int(v.timestamp()),
        },
        extra="allow",
    )


class CommonSSEEvent(BaseSSEEvent):
    """通用事件"""
    event: str
    data: CommonEventData


class MessageEventData(BaseEventData):
    """消息事件数据"""
    role: Literal["user", "assistant"] = "assistant"
    message: str = ""
    attachments: List[File] = Field(default_factory=list)
    stream_id: Optional[str] = None


class MessageSSEEvent(BaseSSEEvent):
    """流式消息事件数据响应结构"""
    event: Literal["message"] = "message"
    data: MessageEventData


class TitleEventData(BaseEventData):
    """标题事件数据"""
    title: str


class TitleSSEEvent(BaseSSEEvent):
    """标题流式事件"""
    event: Literal["title"] = "title"
    data: TitleEventData


class ClarifyOptionData(BaseModel):
    """澄清问题选项数据"""
    id: str
    label: str


class ClarifyQuestionData(BaseModel):
    """澄清问题数据"""
    id: str
    prompt: str
    options: List[ClarifyOptionData] = Field(default_factory=list)
    allow_multiple: bool = False
    allow_custom: bool = True


class ClarifyEventData(BaseEventData):
    """澄清事件数据"""
    title: Optional[str] = None
    questions: List[ClarifyQuestionData] = Field(default_factory=list)


class ClarifySSEEvent(BaseSSEEvent):
    """澄清流式事件"""
    event: Literal["clarify"] = "clarify"
    data: ClarifyEventData


class StepEventData(BaseEventData):
    """步骤事件数据"""
    id: str  # 步骤id
    status: ExecutionStatus  # 步骤执行状态
    description: str  # 步骤描述
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None


class StepSSEEvent(BaseSSEEvent):
    """步骤流式事件"""
    event: Literal["step"] = "step"
    data: StepEventData


class SubAgentEventData(BaseEventData):
    """子 Agent 委派事件数据"""
    subagent_id: str
    goal: str
    status: Literal["started", "completed", "failed"] = "started"
    result_preview: Optional[str] = None
    error: Optional[str] = None


class SubAgentSSEEvent(BaseSSEEvent):
    """子 Agent 流式事件"""
    event: Literal["subagent"] = "subagent"
    data: SubAgentEventData


class PlanEventData(BaseEventData):
    """计划事件数据"""
    steps: List[StepEventData]


class PlanSSEEvent(BaseSSEEvent):
    """计划流式事件"""
    event: Literal["plan"] = "plan"
    data: PlanEventData


class ToolEventData(BaseEventData):
    """工具事件数据"""
    tool_call_id: str  # 工具调用id
    name: str  # 工具箱名字
    status: ToolEventStatus  # 工具状态
    function: str  # 工具名字
    args: Dict[str, Any]  # 工具参数
    content: Optional[Any] = None  # 工具调用结果
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None


class ToolSSEEvent(BaseSSEEvent):
    """工具流式事件"""
    event: Literal["tool"] = "tool"
    data: ToolEventData


class DoneSSEEvent(BaseSSEEvent):
    """停止流式事件"""
    event: Literal["done"] = "done"


class WaitSSEEvent(BaseSSEEvent):
    """等待人类输入流式事件"""
    event: Literal["wait"] = "wait"


class ErrorEventData(BaseEventData):
    """错误事件数据"""
    error: str
    code: Optional[str] = None


class ErrorSSEEvent(BaseSSEEvent):
    """错误流式事件"""
    event: Literal["error"] = "error"
    data: ErrorEventData


class UsageEventData(BaseEventData):
    """Token 用量事件数据"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    call_count: int = 0
    delta_prompt_tokens: int = 0
    delta_completion_tokens: int = 0


class UsageSSEEvent(BaseSSEEvent):
    """Token 用量流式事件"""
    event: Literal["usage"] = "usage"
    data: UsageEventData


class AssistantNoticeEventData(BaseEventData):
    """助手提示事件数据"""
    message: str = ""
    i18n_key: Optional[str] = None
    i18n_params: Optional[Dict[str, str]] = None


class AssistantNoticeSSEEvent(BaseSSEEvent):
    """助手提示流式事件"""
    event: Literal["assistant_notice"] = "assistant_notice"
    data: AssistantNoticeEventData


class SessionStatusEventData(BaseEventData):
    """会话状态事件数据"""
    status: Literal["pending", "running", "waiting", "completed", "cancelled", "failed"] = "running"


class SessionStatusSSEEvent(BaseSSEEvent):
    """会话状态流式事件"""
    event: Literal["session_status"] = "session_status"
    data: SessionStatusEventData


class DebugItemEventData(BaseEventData):
    """调试项事件数据"""
    item_type: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class DebugItemSSEEvent(BaseSSEEvent):
    """调试项流式事件"""
    event: Literal["debug_item"] = "debug_item"
    data: DebugItemEventData


class ArtifactEventData(BaseEventData):
    """交付物事件数据"""
    artifact_id: str
    kind: Literal["doc", "web"] = "doc"
    title: str = ""
    status: Literal["draft", "updated", "final"] = "draft"
    storage_ref: str = ""
    version: int = 1


class ArtifactSSEEvent(BaseSSEEvent):
    """交付物流式事件"""
    event: Literal["artifact"] = "artifact"
    data: ArtifactEventData


class ApprovalEventData(BaseEventData):
    """审批门控事件数据"""
    approval_id: str
    kind: Literal["plan", "tool", "takeover"] = "plan"
    payload: Dict[str, Any] = Field(default_factory=dict)
    options: List[str] = Field(default_factory=list)


class ApprovalSSEEvent(BaseSSEEvent):
    """审批门控流式事件"""
    event: Literal["approval"] = "approval"
    data: ApprovalEventData


class MessageDeltaEventData(BaseEventData):
    """消息增量事件数据"""
    stream_id: str
    role: Literal["user", "assistant"] = "assistant"
    delta: str = ""


class MessageDeltaSSEEvent(BaseSSEEvent):
    """消息增量流式事件"""
    event: Literal["message_delta"] = "message_delta"
    data: MessageDeltaEventData


class ReasoningDeltaEventData(BaseEventData):
    """思考内容增量事件数据"""
    stream_id: str
    delta: str = ""


class ReasoningDeltaSSEEvent(BaseSSEEvent):
    """思考内容增量流式事件"""
    event: Literal["reasoning_delta"] = "reasoning_delta"
    data: ReasoningDeltaEventData


class ToolArgsDeltaEventData(BaseEventData):
    """工具参数增量事件数据"""
    stream_id: str
    tool_call_id: str
    tool_name: str = ""
    delta: str = ""


class ToolArgsDeltaSSEEvent(BaseSSEEvent):
    """工具参数增量流式事件"""
    event: Literal["tool_args_delta"] = "tool_args_delta"
    data: ToolArgsDeltaEventData


# 定义Agent流式事件类型集合
AgentSSEEvent = Union[
    CommonSSEEvent,
    ClarifySSEEvent,
    MessageSSEEvent,
    MessageDeltaSSEEvent,
    ReasoningDeltaSSEEvent,
    ToolArgsDeltaSSEEvent,
    TitleSSEEvent,
    StepSSEEvent,
    SubAgentSSEEvent,
    PlanSSEEvent,
    ToolSSEEvent,
    DoneSSEEvent,
    ErrorSSEEvent,
    UsageSSEEvent,
    WaitSSEEvent,
    AssistantNoticeSSEEvent,
    SessionStatusSSEEvent,
    DebugItemSSEEvent,
    ArtifactSSEEvent,
    ApprovalSSEEvent,
]


@dataclass
class EventMapping:
    """事件映射数据类，用于存储事件映射信息，涵盖流式事件类型、数据类、事件类型字符串"""
    sse_event_class: Type[BaseSSEEvent]
    data_class: Type[BaseEventData]
    event_type: str


class EventMapper:
    """事件映射类，利用Python自身提供的自省机制，将业务逻辑中的Event转换成适合流式传输的AgentSSEEvent"""
    # 缓存映射(type: EventMapping)
    _cache_mapping: Optional[Dict[str, EventMapping]] = None
    _DOMAIN_EXCLUDE_FIELDS = {
        "id",
        "type",
        "created_at",
        "schema_version",
        "visibility",
        "channel",
        "persist",
    }

    @staticmethod
    def event_payload_data(event: Event) -> Dict[str, Any]:
        """将领域事件投影为 SSE data 载荷中除 EventMeta 外的业务字段。"""
        if isinstance(event, StepEvent):
            return {
                "id": event.step.id,
                "status": event.step.status,
                "description": event.step.description,
                "started_at": event.started_at,
                "ended_at": event.ended_at,
                "duration_ms": event.duration_ms,
                "error": event.error or event.step.error,
                "span_id": event.span_id,
                "parent_span_id": event.parent_span_id,
            }
        if isinstance(event, PlanEvent):
            return {
                "steps": [
                    StepEventData(
                        **BaseEventData.base_event_data(event),
                        id=step.id,
                        status=step.status,
                        description=step.description,
                    )
                    for step in event.plan.steps
                ]
            }
        if isinstance(event, ToolEvent):
            return {
                "tool_call_id": event.tool_call_id,
                "name": event.tool_name,
                "status": event.status,
                "function": event.function_name,
                "args": event.function_args,
                "content": event.tool_content,
                "started_at": event.started_at,
                "ended_at": event.ended_at,
                "duration_ms": event.duration_ms,
                "error": event.error,
                "span_id": event.span_id,
                "parent_span_id": event.parent_span_id,
            }

        return event.model_dump(mode="json", exclude=EventMapper._DOMAIN_EXCLUDE_FIELDS)

    @staticmethod
    def _get_event_type_mapping() -> Dict[str, EventMapping]:
        """通过反射动态构建从事件类型字符串到AgentSSEEvent的映射"""
        # 1.判断缓存映射是否存在，如果存在则直接返回
        if EventMapper._cache_mapping is not None:
            return EventMapper._cache_mapping

        # 2.获取AgentSSEEvent的所有可能存在类
        sse_event_classes = get_args(AgentSSEEvent)
        mapping = {}

        # 3.循环遍历AgentSSEEvent可能的所有类逐个处理
        for sse_event_class in sse_event_classes:
            # 4.跳过基类
            if sse_event_class == BaseSSEEvent:
                continue

            # 5.检查类是否包含event属性
            if hasattr(sse_event_class, "__annotations__") and "event" in sse_event_class.__annotations__:
                # 6.提取事件字段
                event_field = sse_event_class.__annotations__["event"]

                # 7.提取事件的具体值(Literal的值)
                if hasattr(event_field, "__args__") and len(event_field.__args__) > 0:
                    event_type = event_field.__args__[0]

                    # 8.提取sse的载荷数据
                    data_class = None
                    if hasattr(sse_event_class, "__annotations__") and "data" in sse_event_class.__annotations__:
                        data_class = sse_event_class.__annotations__["data"]

                    # 9.构建并注册映射关系
                    mapping[event_type] = EventMapping(
                        sse_event_class=sse_event_class,
                        data_class=data_class,
                        event_type=event_type
                    )

        # 10.更新类级缓存
        EventMapper._cache_mapping = mapping
        return mapping

    @staticmethod
    def event_to_sse_event(event: Event) -> AgentSSEEvent:
        """将领域事件转换为Agent流式事件模型"""
        # 1.获取事件映射表
        event_type_mapping = EventMapper._get_event_type_mapping()

        # 2.根据传递进来的事件获取映射类
        event_mapping = event_type_mapping.get(event.type)

        # 3.如果找到了类型映射则进行转换
        if event_mapping:
            sse_event = event_mapping.sse_event_class.from_event(event)
            return sse_event

        # 4.如果没找到类型则使用通用类型
        return CommonSSEEvent.from_event(event)

    @staticmethod
    def events_to_sse_events(
            events: List[Event],
            *,
            include_transient: bool = False,
            include_debug: bool = False,
            include_internal: bool = False,
    ) -> List[AgentSSEEvent]:
        """将领域事件模型列表转换为SSE流式事件列表"""
        replay_events = project_events(
            events,
            include_transient=include_transient,
            include_debug=include_debug,
            include_internal=include_internal,
        )
        return list(filter(lambda x: x is not None, [
            EventMapper.event_to_sse_event(event) for event in replay_events
        ]))
