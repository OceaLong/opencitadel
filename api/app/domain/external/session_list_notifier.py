#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionListNotifierPort(Protocol):
    async def notify_sessions_changed(self) -> None:
        ...


class NoopSessionListNotifier:
    async def notify_sessions_changed(self) -> None:
        return None
