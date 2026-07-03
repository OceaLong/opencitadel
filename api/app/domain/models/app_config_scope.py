#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AppConfig storage scope and merge rules."""
from enum import Enum
from typing import FrozenSet

GLOBAL_CONFIG_ID = "global"
LEGACY_DEFAULT_CONFIG_ID = "default"

# Session-scoped sections that users may override (partial payload only).
USER_OVERRIDABLE_SECTIONS: FrozenSet[str] = frozenset({
    "agent_config",
    "memory",
    "hitl",
    "model_resilience",
    "knowledge_base",
})

# Process-scoped sections; only global admin may edit.
GLOBAL_ONLY_SECTIONS: FrozenSet[str] = frozenset({
    "server",
    "worker",
    "streams",
    "sandbox",
    "scheduler",
    "observability",
    "feature_flags",
})

ALL_APP_CONFIG_SECTIONS: FrozenSet[str] = USER_OVERRIDABLE_SECTIONS | GLOBAL_ONLY_SECTIONS


class AppConfigScope(str, Enum):
    GLOBAL = "global"
    USER = "user"


def user_config_id(user_id: str) -> str:
    return f"user:{user_id}"
