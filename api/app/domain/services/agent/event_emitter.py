#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Event emission: Redis output stream + batched Postgres persistence."""
import asyncio
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
    SessionStatusEvent,
    StepEvent,
    ToolEvent,
    WaitEvent,
)
from app.domain.models.event_policy import should_persist_event
from app.domain.models.resource_governance import ResourceBindingProjection
from app.domain.repositories.uow import IUnitOfWork

logger = logging.getLogger(__name__)

_TERMINAL_SESSION_STATUSES = frozenset(
    {"waiting", "completed", "cancelled", "failed"}
)


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
        self._status_retry_buffer: List[
            Tuple[Task, SessionStatusEvent, Dict[str, Any]]
        ] = []
        self.last_observable_event_id: Optional[str] = None
        self._session_status_lock = asyncio.Lock()
        self._current_run_epoch_id: Optional[str] = None
        self._claimed_terminal_event: Optional[SessionStatusEvent] = None
        self._turn_resource_bindings: tuple[
            ResourceBindingProjection,
            ...,
        ] = ()

    def start_turn(
        self,
        resource_bindings: List[ResourceBindingProjection],
    ) -> None:
        """Pin an immutable metadata snapshot for subsequently emitted events."""
        self._turn_resource_bindings = tuple(
            ResourceBindingProjection.model_validate(binding)
            for binding in resource_bindings
        )

    def _apply_turn_resource_bindings(self, event: BaseEvent) -> None:
        if (
            "resource_bindings" in event.model_fields_set
            or not self._turn_resource_bindings
        ):
            return
        event.resource_bindings = list(self._turn_resource_bindings)

    async def emit(self, task: Task, event: Event) -> bool:
        if isinstance(event, SessionStatusEvent):
            async with self._session_status_lock:
                return await self._emit_session_status_event(task, event)

        await self._emit_event(task, event)
        return True

    async def _claim_session_status(
            self,
            event: SessionStatusEvent,
            event_data: Dict[str, Any],
    ) -> bool:
        async with self._uow_factory() as uow:
            return await uow.session.claim_session_status_event(
                self._session_id,
                event,
                event_data,
            )

    async def _load_authoritative_terminal(
            self,
            run_epoch_id: str,
    ) -> Optional[SessionStatusEvent]:
        page_size = 200
        before: Optional[int] = None
        async with self._uow_factory() as uow:
            while True:
                records = await uow.session.list_events(
                    self._session_id,
                    before=before,
                    limit=page_size,
                    latest=before is None,
                )
                for _seq, persisted_event in reversed(records):
                    if not isinstance(persisted_event, SessionStatusEvent):
                        continue
                    if persisted_event.run_epoch_id != run_epoch_id:
                        continue
                    if persisted_event.status in _TERMINAL_SESSION_STATUSES:
                        return persisted_event
                    if persisted_event.status == "running":
                        return None
                if len(records) < page_size:
                    return None
                before = records[0][0]

    async def _publish_durable_status(
            self,
            task: Task,
            event: SessionStatusEvent,
    ) -> None:
        try:
            stream_message_id = await task.output_stream.put(
                event.model_dump_json(exclude={"outcome"})
            )
        except Exception as exc:
            logger.error(
                "会话状态已落库但流发布失败 session=%s epoch=%s status=%s: %s",
                self._session_id,
                event.run_epoch_id,
                event.status,
                exc,
                exc_info=True,
            )
            return
        try:
            await self._task_state_port.set_output_seq_cursor(
                task.id,
                int(event.id),
                stream_message_id,
            )
        except Exception as exc:
            logger.error(
                "会话状态已落库但游标更新失败 session=%s epoch=%s status=%s: %s",
                self._session_id,
                event.run_epoch_id,
                event.status,
                exc,
                exc_info=True,
            )

    async def _emit_session_status_event(
            self,
            task: Task,
            event: SessionStatusEvent,
    ) -> bool:
        self._apply_turn_resource_bindings(event)
        if event.status == "running":
            event.run_epoch_id = (
                event.run_epoch_id
                or f"{task.id}:{event.id}"
            )
        else:
            event.run_epoch_id = (
                event.run_epoch_id or self._current_run_epoch_id
            )
        if not event.run_epoch_id:
            raise ValueError(
                f"Session status {event.status} requires run_epoch_id"
            )

        seq = await self._event_sequence.allocate()
        event.id = str(seq)
        event_data = event.model_dump(mode="json")
        try:
            accepted = await self._claim_session_status(event, event_data)
        except asyncio.CancelledError:
            if not any(
                buffered_event is event
                for _task, buffered_event, _payload in self._status_retry_buffer
            ):
                self._status_retry_buffer.append((task, event, event_data))
            raise
        except Exception:
            if not any(
                buffered_event is event
                for _task, buffered_event, _payload in self._status_retry_buffer
            ):
                self._status_retry_buffer.append((task, event, event_data))
            raise
        if not accepted:
            if event.status in _TERMINAL_SESSION_STATUSES:
                self._claimed_terminal_event = (
                    await self._load_authoritative_terminal(
                        event.run_epoch_id
                    )
                )
            logger.warning(
                "忽略会话状态竞争 session=%s epoch=%s rejected=%s",
                self._session_id,
                event.run_epoch_id,
                event.status,
            )
            return False

        self._current_run_epoch_id = event.run_epoch_id
        if event.status == "running":
            self._claimed_terminal_event = None
        elif event.status in _TERMINAL_SESSION_STATUSES:
            self._claimed_terminal_event = event
        await self._publish_durable_status(task, event)
        return True

    async def _emit_event(self, task: Task, event: Event) -> None:
        self._apply_turn_resource_bindings(event)
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
            critical = isinstance(
                event,
                (
                    DoneEvent,
                    ErrorEvent,
                    SessionStatusEvent,
                    WaitEvent,
                    StepEvent,
                    ToolEvent,
                ),
            )
            if critical or len(self._persist_buffer) >= self._batch_size:
                await self.flush()

    async def flush(self) -> None:
        if self._status_retry_buffer:
            pending_statuses = list(self._status_retry_buffer)
            for task, event, event_data in pending_statuses:
                accepted = await self._claim_session_status(event, event_data)
                self._status_retry_buffer.remove((task, event, event_data))
                if accepted:
                    self._current_run_epoch_id = event.run_epoch_id
                    if event.status in _TERMINAL_SESSION_STATUSES:
                        self._claimed_terminal_event = event
                    await self._publish_durable_status(task, event)
                elif event.status in _TERMINAL_SESSION_STATUSES:
                    self._claimed_terminal_event = (
                        await self._load_authoritative_terminal(
                            event.run_epoch_id,
                        )
                    )
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

    @property
    def claimed_terminal_status(self) -> Optional[str]:
        if self._claimed_terminal_event is None:
            return None
        return self._claimed_terminal_event.status

    @property
    def claimed_terminal_event(self) -> Optional[SessionStatusEvent]:
        return self._claimed_terminal_event
