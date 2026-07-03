#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Dedicated sub-agent for delegated subtasks (free-form summary output)."""
from typing import Optional

from .base import BaseAgent


class SubAgentAgent(BaseAgent):
    """Lightweight agent for isolated subtask execution."""

    name: str = "subagent"
    _system_prompt: str = ""
    _format: Optional[str] = None
