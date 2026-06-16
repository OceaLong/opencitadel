#!/usr/bin/env python
# -*- coding: utf-8 -*-
from pydantic import BaseModel, Field


class AgentMemoryRuntimeSettings(BaseModel):
    compact_tool_content_max_chars: int = 2000
    compact_strategy: str = "hybrid"
    compact_token_threshold: int = 32000
    compact_keep_recent: int = 12


class AgentRuntimeSettings(BaseModel):
    tool_timeout_seconds: int = 120
    memory: AgentMemoryRuntimeSettings = Field(default_factory=AgentMemoryRuntimeSettings)
