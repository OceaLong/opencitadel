#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.user import User


class UserRepository(ABC):
    @abstractmethod
    async def get_by_id(self, user_id: str) -> Optional[User]:
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
    async def save(self, user: User) -> None:
        ...

    @abstractmethod
    async def delete_by_id(self, user_id: str) -> None:
        ...
