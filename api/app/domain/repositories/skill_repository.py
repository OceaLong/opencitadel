from abc import ABC, abstractmethod

from app.domain.models.scope import OwnerScope
from app.domain.models.skill import Skill


class SkillRepository(ABC):
    @abstractmethod
    async def get_all(
        self, enabled_only: bool = False, scope: OwnerScope | None = None
    ) -> list[Skill]: ...

    @abstractmethod
    async def get_by_id(self, skill_id: str, scope: OwnerScope | None = None) -> Skill | None: ...

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Skill | None: ...

    @abstractmethod
    async def save(self, skill: Skill) -> None: ...

    @abstractmethod
    async def delete_by_id(self, skill_id: str) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...
