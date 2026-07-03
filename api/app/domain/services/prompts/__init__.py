#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Default (English) prompt exports for backward compatibility."""

from app.domain.services.prompts.en.clarify import (
    CLARIFY_AGENT_SYSTEM_PROMPT,
    CLARIFY_PROMPT,
    CLARIFY_SYSTEM_PROMPT,
)
from app.domain.services.prompts.en.planner import (
    CREATE_PLAN_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    UPDATE_PLAN_PROMPT,
)
from app.domain.services.prompts.en.react import EXECUTION_PROMPT, REACT_SYSTEM_PROMPT, SUMMARIZE_PROMPT
from app.domain.services.prompts.en.system import SYSTEM_PROMPT

__all__ = [
    "SYSTEM_PROMPT",
    "PLANNER_SYSTEM_PROMPT",
    "CREATE_PLAN_PROMPT",
    "UPDATE_PLAN_PROMPT",
    "REACT_SYSTEM_PROMPT",
    "EXECUTION_PROMPT",
    "SUMMARIZE_PROMPT",
    "CLARIFY_PROMPT",
    "CLARIFY_SYSTEM_PROMPT",
    "CLARIFY_AGENT_SYSTEM_PROMPT",
]
