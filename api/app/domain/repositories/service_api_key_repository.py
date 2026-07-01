#!/usr/bin/env python
# -*- coding: utf-8 -*-
from abc import ABC, abstractmethod
from typing import List, Optional

from app.domain.models.service_api_key import ServiceApiKey


class ServiceApiKeyRepository(ABC):
    @abstractmethod
    async def get_by_hash(self, key_hash: str) -> Optional[ServiceApiKey]:
        ...

    @abstractmethod
    async def list_for_user(self, user_id: str) -> List[ServiceApiKey]:
        ...

    @abstractmethod
    async def save(self, key: ServiceApiKey) -> None:
        ...

    @abstractmethod
    async def revoke(self, key_id: str, user_id: str) -> None:
        ...
