#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class TaskStatePort(Protocol):
    async def is_cancelled(self, task_id: str) -> bool:
        ...

    async def get_task_meta(self, task_id: str) -> Optional[Dict[str, Any]]:
        ...
