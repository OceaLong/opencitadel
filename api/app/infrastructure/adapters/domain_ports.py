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
            cached_tokens: int = 0,
    ) -> None:
        record_llm_tokens(
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )

    def record_agent_step(self, agent_name: str, step: str) -> None:
        record_agent_step(agent_name, step)

    def create_agent_tracer(self, session_id: str, agent_name: str) -> AgentTracer:
        return AgentTracer(session_id=session_id, agent_name=agent_name)


class RedisTaskStateAdapter(TaskStatePort):
    def __init__(self) -> None:
        self._task_state = get_task_state()

    async def register_task(
            self,
            task_id: str,
            session_id: str,
            task_type: str = "agent",
            resource_id: str = "",
            request_id: str = "",
            run_generation: int = 1,
    ) -> None:
        await self._task_state.register_task(
            task_id,
            session_id=session_id,
            task_type=task_type,
            resource_id=resource_id,
            request_id=request_id,
            run_generation=run_generation,
        )

    async def is_cancelled(self, task_id: str) -> bool:
        return await self._task_state.is_cancelled(task_id)

    async def get_task_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        return await self._task_state.get_task_meta(task_id)

    async def is_done(self, task_id: str) -> bool:
        return await self._task_state.is_done(task_id)

    def heartbeat_is_stale(
            self,
            meta: Optional[Dict[str, Any]],
            stale_after_seconds: float,
    ) -> bool:
        return self._task_state.heartbeat_is_stale(
            meta,
            stale_after_seconds,
        )

    async def get_runtime_snapshot(self, task_id: str) -> Dict[str, Any]:
        return await self._task_state.get_runtime_snapshot(task_id)

    async def set_status(
            self,
            task_id: str,
            run_generation: int,
            status: Any,
    ) -> bool:
        return await self._task_state.set_status(
            task_id,
            run_generation,
            status,
        )

    async def set_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
            run_epoch_id: str,
            outcome: Dict[str, Any],
    ) -> bool:
        return await self._task_state.set_run_reconciliation(
            task_id,
            run_generation,
            run_epoch_id,
            outcome,
        )

    async def get_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
    ) -> Optional[Dict[str, Any]]:
        return await self._task_state.get_run_reconciliation(
            task_id,
            run_generation,
        )

    async def clear_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
    ) -> bool:
        return await self._task_state.clear_run_reconciliation(
            task_id,
            run_generation,
        )

    async def record_heartbeat(
            self,
            task_id: str,
            run_generation: int,
            worker_id: str,
    ) -> bool:
        return await self._task_state.record_heartbeat(
            task_id,
            run_generation,
            worker_id,
        )

    async def set_output_seq_cursor(self, task_id: str, seq: int, stream_id: str) -> None:
        await self._task_state.set_output_seq_cursor(task_id, seq, stream_id)

    async def get_output_seq_cursor(self, task_id: str, seq: int) -> Optional[str]:
        return await self._task_state.get_output_seq_cursor(task_id, seq)

    async def request_cancel(self, task_id: str) -> None:
        await self._task_state.request_cancel(task_id)

    async def clear_cancel(self, task_id: str) -> None:
        await self._task_state.clear_cancel(task_id)

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


def default_session_list_notifier() -> SessionListNotifierPort:
    return RedisSessionListNotifierAdapter()
