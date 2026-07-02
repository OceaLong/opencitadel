#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from pydantic import BaseModel, Field


class AgentMemoryRuntimeSettings(BaseModel):
    compact_tool_content_max_chars: int = 2000
    compact_strategy: str = "hybrid"
    compact_token_threshold: int = 32000
    compact_keep_recent: int = 12
    compact_always_on_step_boundary: bool = True
    compact_rule_trigger_threshold: int = 16000
    tool_output_offload_enabled: bool = False
    tool_output_offload_threshold_chars: int = 4000


class AgentRuntimeSettings(BaseModel):
    tool_timeout_seconds: int = 120
    tool_gate_call_level_enabled: Optional[bool] = None
    memory: AgentMemoryRuntimeSettings = Field(default_factory=AgentMemoryRuntimeSettings)
