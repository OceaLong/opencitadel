#!/usr/bin/env python
# -*- coding: utf-8 -*-
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field

from .event import Event, PlanEvent
from .file import File
from .memory import Memory
from .plan import Plan
from .skill import SkillSummary
from .codebase import SessionMode


class SessionStatus(str, Enum):
    """会话状态类型枚举"""
    PENDING = "pending"  # 等待任务
    RUNNING = "running"  # 运行中
    WAITING = "waiting"  # 等待人类响应
    COMPLETED = "completed"  # 已完成
    CANCELLED = "cancelled"  # 用户取消
    FAILED = "failed"  # 执行失败


class Session(BaseModel):
    """会话领域模型"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # 会话id
    sandbox_id: Optional[str] = None  # 沙箱id
    task_id: Optional[str] = None  # 任务id
    title: str = ""  # 标题
    unread_message_count: int = 0  # 未读消息数
    latest_message: str = ""  # 最新消息
    latest_message_at: Optional[datetime] = None  # 最新消息时间
    events: List[Event] = Field(default_factory=list)  # 事件列表
    files: List[File] = Field(default_factory=list)  # 文件列表
    memories: Dict[str, Memory] = Field(default_factory=dict)  # 记忆
    model_id: Optional[str] = None  # 会话级模型id，null使用全局默认
    skill_id: Optional[str] = None  # 会话级Skill id，null表示不启用
    thinking_enabled: bool = False  # 会话级思考模式，默认关闭
    codebase_id: Optional[str] = None  # 关联代码库
    knowledge_base_id: Optional[str] = None  # 关联文档知识库
    owner_user_id: Optional[str] = None  # 所属用户
    team_id: Optional[str] = None  # 所属团队工作区
    mode: SessionMode = SessionMode.AGENT  # ask=快速问答, agent=规划改码
    pending_phase: Optional[str] = None  # 等待恢复的内部阶段
    pending_metadata: Optional[Dict[str, Any]] = None  # 门控状态细节（Plan、tool call 等）
    operator_scope: Optional[str] = None  # owned | third_party_saas — Web Operator 目标系统归属
    operator_domains: List[str] = Field(default_factory=list)  # 域名白名单
    gate_profile: Optional[str] = None  # loose | standard | strict
    status: SessionStatus = SessionStatus.PENDING  # 状态
    updated_at: datetime = Field(default_factory=datetime.now)  # 更新时间
    created_at: datetime = Field(default_factory=datetime.now)  # 创建时间

    def get_latest_plan(self) -> Optional[Plan]:
        """获取会话中的最新计划"""
        # 1.倒序遍历会话中所有事件消息
        for event in reversed(self.events):
            # 2.判断事件的类型是否为PlanEvent，如果是则提取计划后返回
            if isinstance(event, PlanEvent):
                return event.plan

        return None
