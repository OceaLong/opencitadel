#!/usr/bin/env python
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from typing import Callable, List

from app.domain.models.service_api_key import ServiceApiKey
from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.security.service_api_key import ServiceApiKeyHasher


@dataclass(frozen=True)
class CreatedServiceApiKey:
    key: ServiceApiKey
    plaintext: str


class ServiceApiKeyService:
    def __init__(
            self,
            uow_factory: Callable[[], IUnitOfWork],
            hasher: ServiceApiKeyHasher,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher

    async def create_key(self, *, user_id: str, name: str) -> CreatedServiceApiKey:
        generated = self._hasher.generate()
        key = ServiceApiKey(
            owner_user_id=user_id,
            name=name,
            key_hash=generated.key_hash,
            prefix=generated.prefix,
        )
        async with self._uow_factory() as uow:
            await uow.service_api_key.save(key)
        return CreatedServiceApiKey(key=key, plaintext=generated.plaintext)

    async def list_keys(self, user_id: str) -> List[ServiceApiKey]:
        async with self._uow_factory() as uow:
            return await uow.service_api_key.list_for_user(user_id)

    async def revoke_key(self, *, user_id: str, key_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.service_api_key.revoke(key_id, user_id)
