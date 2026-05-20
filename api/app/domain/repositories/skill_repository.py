#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.skill import Skill


class SkillRepository(ABC):
    @abstractmethod
    async def get_all(self, enabled_only: bool = False) -> List[Skill]:
        ...

    @abstractmethod
    async def get_by_id(self, skill_id: str) -> Optional[Skill]:
        ...

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Optional[Skill]:
        ...

    @abstractmethod
    async def save(self, skill: Skill) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, skill_id: str) -> None:
        ...

    @abstractmethod
    async def count(self) -> int:
        ...
