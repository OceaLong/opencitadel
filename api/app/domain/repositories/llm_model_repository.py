#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.llm_model import LLMModel


class LLMModelRepository(ABC):
    @abstractmethod
    async def get_all(self) -> List[LLMModel]:
        ...

    @abstractmethod
    async def get_by_id(self, model_id: str) -> Optional[LLMModel]:
        ...

    @abstractmethod
    async def get_default(self) -> Optional[LLMModel]:
        ...

    @abstractmethod
    async def save(self, model: LLMModel, encrypted_api_key: str) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, model_id: str) -> None:
        ...

    @abstractmethod
    async def clear_default(self) -> None:
        ...

    @abstractmethod
    async def count(self) -> int:
        ...
