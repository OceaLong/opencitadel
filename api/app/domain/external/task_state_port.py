#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class TaskStatePort(Protocol):
    async def is_cancelled(self, task_id: str) -> bool:
        ...

    async def get_task_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    async def get_runtime_snapshot(self, task_id: str) -> Dict[str, Any]:
        """Single round-trip snapshot: cancelled flag, status, is_done."""
        ...

    async def record_heartbeat(self, task_id: str, worker_id: str) -> None:
        ...

    async def set_output_seq_cursor(self, task_id: str, seq: int, stream_id: str) -> None:
        ...

    async def get_output_seq_cursor(self, task_id: str, seq: int) -> Optional[str]:
        ...

    async def request_cancel(self, task_id: str) -> None:
        ...

    async def wait_for_cancel(self, task_id: str, timeout_seconds: float = 30.0) -> bool:
        ...
