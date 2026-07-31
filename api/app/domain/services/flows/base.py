#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from enum import Enum
from typing import AsyncGenerator

from app.domain.models.event import BaseEvent
from app.domain.models.message import Message
from app.domain.models.run_outcome import RunOutcome


class FlowStatus(str, Enum):
    """流状态类型枚举"""
    IDLE = "idle"  # 空闲中
    CLARIFYING = "clarifying"  # 澄清中
    PLANNING = "planning"  # 规划中
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"  # 等待计划审批
    EXECUTING = "executing"  # 执行中
    UPDATING = "updating"  # 更新中
    SUMMARIZING = "summarizing"  # 汇总中
    COMPLETED = "completed"  # 已完成


class BaseFlow(ABC):
    """基础流抽象类"""

    @property
    def outcome(self) -> RunOutcome:
        if not hasattr(self, "_outcome"):
            self._reset_outcome()
        return self._outcome

    def _reset_outcome(self) -> None:
        self._outcome = RunOutcome.failed(
            "Flow ended without an explicit outcome",
            code="FLOW_OUTCOME_UNSET",
        )

    def _mark_succeeded(self) -> None:
        self._outcome = RunOutcome.succeeded()

    def _mark_failed(self, message: str, code: str | None = None) -> None:
        self._outcome = RunOutcome.failed(message, code=code)

    def _mark_cancelled(self) -> None:
        self._outcome = RunOutcome.cancelled()

    def _mark_waiting(self) -> None:
        self._outcome = RunOutcome.waiting()

    @abstractmethod
    async def invoke(self, message: Message) -> AsyncGenerator[BaseEvent, None]:
        """流调用函数，返回可迭代的基础事件"""
        ...

    @property
    @abstractmethod
    def done(self) -> bool:
        """只读属性，用于返回流是否结束"""
        ...
