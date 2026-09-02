"""Kernel context values for HTTP and worker processes."""

from dataclasses import dataclass
from typing import Any

from app.kernel.application.command_service import CommandService
from app.kernel.application.effect_worker import EffectWorker
from app.kernel.application.retention_worker import RetentionWorker
from app.kernel.application.timer_worker import TimerWorker


@dataclass(frozen=True)
class KernelApiRuntime:
    commands: CommandService
    queries: Any
    dispositions: Any
    catalog: Any = None


@dataclass(frozen=True)
class KernelWorkerRuntime:
    commands: CommandService
    effects: EffectWorker
    timers: TimerWorker
    retention: RetentionWorker
