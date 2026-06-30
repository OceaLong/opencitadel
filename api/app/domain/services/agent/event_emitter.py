#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event emission: Redis output stream + batched Postgres persistence."""
import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.domain.external.event_sequence import EventSequencePort
from app.domain.external.task import Task
from app.domain.external.task_state_port import TaskStatePort
from app.domain.models.event import (
    BaseEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    StepEvent,
    ToolEvent,
    WaitEvent,
)
from app.domain.models.event_policy import should_persist_event
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)


class AgentEventEmitter:
    """Handles event seq allocation, Redis output, and batched DB persistence."""

    def __init__(
            self,
            session_id: str,
            uow_factory: Callable[[], IUnitOfWork],
            event_sequence: EventSequencePort,
            task_state_port: TaskStatePort,
            batch_size: int = 10,
    ) -> None:
        self._session_id = session_id
        self._uow_factory = uow_factory
        self._event_sequence = event_sequence
        self._task_state_port = task_state_port
        self._batch_size = batch_size
        self._persist_buffer: List[Tuple[BaseEvent, Dict[str, Any]]] = []
        self.last_observable_event_id: Optional[str] = None

    async def emit(self, task: Task, event: Event) -> None:
        persist = should_persist_event(event)
        if persist:
            seq = await self._event_sequence.allocate()
            event.id = str(seq)
        else:
            event.id = f"t-{uuid.uuid4()}"
        event_data = event.model_dump(mode="json")
        stream_message_id = await task.output_stream.put(event.model_dump_json())
        if isinstance(event, (StepEvent, ToolEvent)):
            self.last_observable_event_id = event.id

        if persist:
            await self._task_state_port.set_output_seq_cursor(
                task.id,
                int(event.id),
                stream_message_id,
            )
            self._persist_buffer.append((event, event_data))
            critical = isinstance(event, (DoneEvent, ErrorEvent, WaitEvent, StepEvent, ToolEvent))
            if critical or len(self._persist_buffer) >= self._batch_size:
                await self.flush()

    async def flush(self) -> None:
        if not self._persist_buffer:
            return
        payloads = self._persist_buffer
        try:
            async with self._uow_factory() as uow:
                await uow.session.add_event_payloads(self._session_id, payloads)
            self._persist_buffer = []
        except Exception as exc:
            logger.error(
                "事件批量落库失败 session_id=%s pending_count=%s: %s",
                self._session_id,
                len(payloads),
                exc,
                exc_info=True,
            )
            raise
