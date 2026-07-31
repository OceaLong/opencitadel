#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class TaskStatePort(Protocol):
    async def register_task(
            self,
            task_id: str,
            session_id: str,
            task_type: str = "agent",
            resource_id: str = "",
            request_id: str = "",
            run_generation: int = 1,
    ) -> None:
        ...

    async def is_cancelled(self, task_id: str) -> bool:
        ...

    async def get_task_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...

    async def is_done(self, task_id: str) -> bool:
        ...

    def heartbeat_is_stale(
            self,
            meta: Optional[Dict[str, Any]],
            stale_after_seconds: float,
    ) -> bool:
        ...

    async def get_runtime_snapshot(self, task_id: str) -> Dict[str, Any]:
        """Single round-trip snapshot: cancelled flag, status, is_done."""
        ...

    async def set_status(
            self,
            task_id: str,
            run_generation: int,
            status: Any,
    ) -> bool:
        ...

    async def set_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
            run_epoch_id: str,
            outcome: Dict[str, Any],
    ) -> bool:
        ...

    async def get_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
    ) -> Optional[Dict[str, Any]]:
        ...

    async def clear_run_reconciliation(
            self,
            task_id: str,
            run_generation: int,
    ) -> bool:
        ...

    async def record_heartbeat(
            self,
            task_id: str,
            run_generation: int,
            worker_id: str,
    ) -> bool:
        ...

    async def set_output_seq_cursor(self, task_id: str, seq: int, stream_id: str) -> None:
        ...

    async def get_output_seq_cursor(self, task_id: str, seq: int) -> Optional[str]:
        ...

    async def request_cancel(self, task_id: str) -> None:
        ...

    async def clear_cancel(self, task_id: str) -> None:
        ...

    async def wait_for_cancel(self, task_id: str, timeout_seconds: float = 30.0) -> bool:
        ...
