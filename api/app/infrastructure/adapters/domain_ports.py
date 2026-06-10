#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional

from app.domain.external.event_sequence import EventSequencePort
from app.domain.external.observability import ObservabilityPort
from app.domain.external.session_list_notifier import SessionListNotifierPort
from app.domain.external.task_state_port import TaskStatePort
from app.infrastructure.external.task.task_state import get_task_state
from app.infrastructure.observability.agent_tracer import AgentTracer
from app.infrastructure.observability.otel import (
    record_agent_cancel,
    record_agent_step,
    record_llm_tokens,
)


class OtelObservabilityAdapter(ObservabilityPort):
    def record_agent_cancel(self, session_id: str) -> None:
        record_agent_cancel(session_id)

    def record_llm_tokens(
            self,
            model: str,
            *,
            prompt_tokens: int,
            completion_tokens: int,
    ) -> None:
        record_llm_tokens(model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    def record_agent_step(self, agent_name: str, step: str) -> None:
        record_agent_step(agent_name, step)

    def create_agent_tracer(self, session_id: str, agent_name: str) -> AgentTracer:
        return AgentTracer(session_id=session_id, agent_name=agent_name)


class RedisTaskStateAdapter(TaskStatePort):
    def __init__(self) -> None:
        self._task_state = get_task_state()

    async def is_cancelled(self, task_id: str) -> bool:
        return await self._task_state.is_cancelled(task_id)

    async def get_task_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        return await self._task_state.get_task_meta(task_id)

    async def get_runtime_snapshot(self, task_id: str) -> Dict[str, Any]:
        return await self._task_state.get_runtime_snapshot(task_id)

    async def set_output_seq_cursor(self, task_id: str, seq: int, stream_id: str) -> None:
        await self._task_state.set_output_seq_cursor(task_id, seq, stream_id)

    async def get_output_seq_cursor(self, task_id: str, seq: int) -> Optional[str]:
        return await self._task_state.get_output_seq_cursor(task_id, seq)

    async def request_cancel(self, task_id: str) -> None:
        await self._task_state.request_cancel(task_id)

    async def wait_for_cancel(self, task_id: str, timeout_seconds: float = 30.0) -> bool:
        return await self._task_state.wait_for_cancel(task_id, timeout_seconds)


class RedisEventSequenceAdapter(EventSequencePort):
    async def allocate(self) -> int:
        from app.infrastructure.external.event_seq_allocator import allocate_event_seq

        return await allocate_event_seq()


class RedisSessionListNotifierAdapter(SessionListNotifierPort):
    async def notify_sessions_changed(self) -> None:
        from app.infrastructure.external.session_list_notifier import notify_sessions_changed

        await notify_sessions_changed()


def default_observability() -> ObservabilityPort:
    return OtelObservabilityAdapter()


def default_task_state() -> TaskStatePort:
    return RedisTaskStateAdapter()


def default_event_sequence() -> EventSequencePort:
    return RedisEventSequenceAdapter()


def default_session_list_notifier() -> SessionListNotifierPort:
    return RedisSessionListNotifierAdapter()
