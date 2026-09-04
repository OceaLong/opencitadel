from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from random import uniform

logger = logging.getLogger(__name__)

TaskFactory = Callable[[], Awaitable[None]]
CriticalFailureHandler = Callable[["TaskFailure"], None]


class TaskKind(StrEnum):
    CRITICAL = "critical"
    AUXILIARY = "auxiliary"


class TaskState(StrEnum):
    RUNNING = "running"
    RESTARTING = "restarting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class RestartPolicy:
    initial_seconds: float = 0.25
    maximum_seconds: float = 5.0
    multiplier: float = 2.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        if self.initial_seconds < 0:
            raise ValueError("initial_seconds must not be negative")
        if self.maximum_seconds < self.initial_seconds:
            raise ValueError("maximum_seconds must be at least initial_seconds")
        if self.multiplier < 1:
            raise ValueError("multiplier must be at least 1")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")


@dataclass(frozen=True)
class TaskFailure:
    name: str
    error: BaseException
    attempts: int


@dataclass(frozen=True)
class TaskReport:
    name: str
    kind: TaskKind
    state: TaskState
    attempts: int
    error: BaseException | None = None


@dataclass
class _TaskRecord:
    name: str
    factory: TaskFactory
    kind: TaskKind
    restart: RestartPolicy | None
    task: asyncio.Task[None] | None = None
    state: TaskState = TaskState.RUNNING
    attempts: int = 0
    error: BaseException | None = None
    started: asyncio.Event | None = None

    def report(self) -> TaskReport:
        return TaskReport(
            name=self.name,
            kind=self.kind,
            state=self.state,
            attempts=self.attempts,
            error=self.error,
        )


class TaskSupervisor:
    """Own every task that may outlive the method which starts it."""

    def __init__(
        self,
        *,
        shutdown_timeout_seconds: float = 30.0,
        on_critical_failure: CriticalFailureHandler | None = None,
    ) -> None:
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        self._records: dict[str, _TaskRecord] = {}
        self._critical_failures: asyncio.Queue[TaskFailure] = asyncio.Queue(maxsize=1)
        self._stop_event = asyncio.Event()
        self._stopping = False
        self._ready = True
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._on_critical_failure = on_critical_failure
        self._stop_reports: Mapping[str, TaskReport] | None = None

    @property
    def stop_event(self) -> asyncio.Event:
        return self._stop_event

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def pending_names(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, record in self._records.items()
            if record.task is not None and not record.task.done()
        )

    def request_stop(self) -> None:
        self._ready = False
        self._stop_event.set()

    async def start(
        self,
        name: str,
        factory: TaskFactory,
        *,
        kind: TaskKind,
        restart: RestartPolicy | None = None,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("task name must not be blank")
        if normalized_name in self._records:
            raise ValueError(f"task[{normalized_name}] is already registered")
        if self._stopping:
            raise RuntimeError("task supervisor is stopping")
        if kind is TaskKind.CRITICAL and restart is not None:
            raise ValueError("critical tasks cannot have a restart policy")

        record = _TaskRecord(
            name=normalized_name,
            factory=factory,
            kind=kind,
            restart=restart,
            started=asyncio.Event(),
        )
        self._records[normalized_name] = record
        record.task = asyncio.create_task(
            self._run(record),
            name=f"opencitadel:{normalized_name}",
        )
        await record.started.wait()

    async def start_transient(self, name: str, factory: TaskFactory) -> None:
        await self.start(name, factory, kind=TaskKind.AUXILIARY)
        normalized_name = name.strip()
        record = self._records[normalized_name]
        assert record.task is not None
        if record.task.done():
            self._forget_completed_transient(normalized_name, record.task)
        else:
            record.task.add_done_callback(
                lambda task: self._forget_completed_transient(normalized_name, task)
            )

    async def wait_for_critical_failure(self) -> TaskFailure:
        return await self._critical_failures.get()

    async def stop(self) -> Mapping[str, TaskReport]:
        """Three-phase graceful drain (D5/K2-3).

        1. Set the stop event: cooperative workers stop claiming new work and
           exit their loops after finishing in-flight handlers.
        2. Wait up to ``shutdown_timeout_seconds`` for tasks to complete on
           their own — an in-flight model call finishes instead of being
           aborted, so its Run is not spuriously failed by a rollout.
        3. Only then cancel whatever is still running, with a short bounded
           grace for the cancellation to land; stragglers that also ignore
           cancellation are reported as TIMED_OUT.
        """
        if self._stop_reports is not None:
            return self._stop_reports

        self._stopping = True
        self.request_stop()
        await asyncio.sleep(0)

        active = [
            record.task
            for record in self._records.values()
            if record.task is not None and not record.task.done()
        ]
        if active:
            _, pending = await asyncio.wait(
                active,
                timeout=self._shutdown_timeout_seconds,
            )
            for task in pending:
                task.cancel()
            if pending:
                _, stuck = await asyncio.wait(
                    pending,
                    timeout=min(self._shutdown_timeout_seconds, 5.0),
                )
                for task in stuck:
                    record = self._record_for_task(task)
                    record.state = TaskState.TIMED_OUT
                    record.error = TimeoutError(
                        f"task[{record.name}] exceeded the shutdown timeout"
                    )

        self._stop_reports = {name: record.report() for name, record in self._records.items()}
        return self._stop_reports

    async def _run(self, record: _TaskRecord) -> None:
        delay = record.restart.initial_seconds if record.restart is not None else 0
        if record.started is None:
            raise RuntimeError("task record has no startup acknowledgement")
        record.started.set()
        while not self._stop_event.is_set():
            record.attempts += 1
            record.state = TaskState.RUNNING
            try:
                await record.factory()
            except asyncio.CancelledError:
                if record.state is not TaskState.TIMED_OUT:
                    record.state = TaskState.CANCELLED
                raise
            except Exception as error:  # noqa: BLE001 - supervised task boundary
                record.error = error
                if record.restart is not None and not self._stop_event.is_set():
                    record.state = TaskState.RESTARTING
                    await self._wait_before_restart(delay, record.restart)
                    delay = min(
                        record.restart.maximum_seconds,
                        max(record.restart.initial_seconds, delay * record.restart.multiplier),
                    )
                    continue
                record.state = TaskState.FAILED
                if record.kind is TaskKind.CRITICAL:
                    self._report_critical_failure(record, error)
                return
            else:
                if record.state is not TaskState.TIMED_OUT:
                    record.state = TaskState.COMPLETED
                return

        if record.state is not TaskState.TIMED_OUT:
            record.state = TaskState.COMPLETED

    async def _wait_before_restart(
        self,
        delay: float,
        policy: RestartPolicy,
    ) -> None:
        jitter = delay * policy.jitter
        wait_seconds = max(0, delay + uniform(-jitter, jitter))
        if wait_seconds == 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=wait_seconds)
        except TimeoutError:
            return

    def _report_critical_failure(
        self,
        record: _TaskRecord,
        error: BaseException,
    ) -> None:
        failure = TaskFailure(
            name=record.name,
            error=error,
            attempts=record.attempts,
        )
        self.request_stop()
        if self._critical_failures.empty():
            self._critical_failures.put_nowait(failure)
        if self._on_critical_failure is not None:
            try:
                self._on_critical_failure(failure)
            except Exception:
                logger.exception(
                    "Critical task failure callback failed for task[%s]",
                    record.name,
                )

    def _record_for_task(self, task: asyncio.Task[None]) -> _TaskRecord:
        for record in self._records.values():
            if record.task is task:
                return record
        raise RuntimeError("supervisor lost an owned task record")

    def _forget_completed_transient(
        self,
        name: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._stopping:
            return
        record = self._records.get(name)
        if record is not None and record.task is task and task.done():
            self._records.pop(name, None)
