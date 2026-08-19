#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Protocol, runtime_checkable


@runtime_checkable
class EventSequencePort(Protocol):
    async def allocate(self) -> int:
        ...
