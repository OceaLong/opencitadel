#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional

from app.domain.models.scope import OwnerScope


class LLMModelPreferenceRepository(ABC):
    """Stores effective model choices separately from mutable model records."""

    @abstractmethod
    async def get_model_id(self, scope: Optional[OwnerScope]) -> Optional[str]:
        ...

    @abstractmethod
    async def set_model_id(
        self,
        scope: Optional[OwnerScope],
        model_id: str,
    ) -> None:
        ...
