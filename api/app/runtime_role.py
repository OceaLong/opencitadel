#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Explicit process role for API, Worker, and Migrate entrypoints."""
from __future__ import annotations

import os
from enum import Enum

_ROLE_ENV = "OPENCITADEL_PROCESS_ROLE"


class ProcessRole(str, Enum):
    API = "api"
    WORKER = "worker"
    MIGRATE = "migrate"


_current_role: ProcessRole | None = None


def set_role(role: ProcessRole) -> None:
    global _current_role
    _current_role = role
    os.environ[_ROLE_ENV] = role.value


def get_role() -> ProcessRole:
    global _current_role
    if _current_role is not None:
        return _current_role
    raw = os.environ.get(_ROLE_ENV, ProcessRole.API.value)
    try:
        _current_role = ProcessRole(raw)
    except ValueError:
        _current_role = ProcessRole.API
    return _current_role
