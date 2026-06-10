#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.llm_token_usage import LLMTokenUsage, SessionTokenUsageSummary


class LLMTokenUsageRepository(ABC):
    @abstractmethod
    async def save(self, usage: LLMTokenUsage) -> None:
        ...

    @abstractmethod
    async def save_many(self, usages: List[LLMTokenUsage]) -> None:
        ...

    @abstractmethod
    async def list_by_session(self, session_id: str) -> List[LLMTokenUsage]:
        ...

    @abstractmethod
    async def aggregate_by_session(self, session_id: str) -> SessionTokenUsageSummary:
        ...
