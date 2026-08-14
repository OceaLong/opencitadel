#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from app.domain.models.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_id(self, user_id: str) -> Optional[User]:
        ...

    @abstractmethod
    async def list_by_ids(self, user_ids: List[str]) -> List[User]:
        ...

    @abstractmethod
    async def get_by_email(self, email: str) -> Optional[User]:
        ...

    @abstractmethod
    async def get_by_username(self, username: str) -> Optional[User]:
        ...

    @abstractmethod
    async def list(self, limit: int = 100, offset: int = 0) -> List[User]:
        ...

    @abstractmethod
    async def count(self) -> int:
        ...

    @abstractmethod
    async def count_by_role(self) -> Dict[str, int]:
        """Count users grouped by global_role (e.g. {'admin': 1, 'user': 3})."""
        ...

    @abstractmethod
    async def save(self, user: User) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, user_id: str) -> None:
        ...
