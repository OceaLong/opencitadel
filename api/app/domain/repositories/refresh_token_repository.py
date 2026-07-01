#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import Optional

from app.domain.models.refresh_token import RefreshToken


class RefreshTokenRepository(ABC):
    @abstractmethod
    async def get_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        ...

    @abstractmethod
    async def save(self, token: RefreshToken) -> None:
        ...

    @abstractmethod
    async def revoke_by_hash(self, token_hash: str) -> None:
        ...

    @abstractmethod
    async def consume_by_hash(self, token_hash: str) -> Optional[RefreshToken]:
        """Atomically revoke a live refresh token and return it if this call consumed it."""
        ...

    @abstractmethod
    async def revoke_all_for_user(self, user_id: str) -> None:
        ...
