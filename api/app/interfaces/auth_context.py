#!/usr/bin/env python
# -*- coding: utf-8 -*-
from contextvars import ContextVar
from typing import Optional

from app.domain.models.scope import Principal


current_principal: ContextVar[Optional[Principal]] = ContextVar("current_principal", default=None)


def get_principal() -> Optional[Principal]:
    return current_principal.get()


def set_principal(principal: Optional[Principal]):
    return current_principal.set(principal)
