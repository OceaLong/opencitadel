#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Awaitable, Callable, Protocol, runtime_checkable

EventSeqAllocator = Callable[[], Awaitable[int]]


@runtime_checkable
class EventSequencePort(Protocol):
    async def allocate(self) -> int:
        ...
