#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class ObservabilityPort(Protocol):
    def record_agent_cancel(self, session_id: str) -> None:
        ...

    def record_llm_tokens(
            self,
            model: str,
            *,
            prompt_tokens: int,
            completion_tokens: int,
    ) -> None:
        ...

    def record_agent_step(self, agent_name: str, step: str) -> None:
        ...

    def create_agent_tracer(self, session_id: str, agent_name: str) -> Any:
        ...
