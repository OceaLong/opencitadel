from collections.abc import Callable
from dataclasses import dataclass

from app.application.ports.crypto import ServiceKeyPort
from app.domain.errors import NotFoundError
from app.domain.models.service_api_key import ServiceApiKey
from app.domain.repositories.uow import IUnitOfWork


@dataclass(frozen=True)
class CreatedServiceApiKey:
    key: ServiceApiKey
    plaintext: str


class ServiceApiKeyService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        hasher: ServiceKeyPort,
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
            await uow.commit()
        return CreatedServiceApiKey(key=key, plaintext=generated.plaintext)

    async def list_keys(self, user_id: str) -> list[ServiceApiKey]:
        async with self._uow_factory() as uow:
            return await uow.service_api_key.list_for_user(user_id)

    async def rotate_key(self, *, user_id: str, key_id: str) -> CreatedServiceApiKey:
        """换发同一 Key 的密钥材料：旧明文立即失效，返回新明文（仅此一次可见）。"""
        generated = self._hasher.generate()
        async with self._uow_factory() as uow:
            key = await uow.service_api_key.rotate(
                key_id,
                user_id,
                key_hash=generated.key_hash,
                prefix=generated.prefix,
            )
            if key is None:
                raise NotFoundError("服务 API Key 不存在或已撤销")
            await uow.commit()
        return CreatedServiceApiKey(key=key, plaintext=generated.plaintext)

    async def revoke_key(self, *, user_id: str, key_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.service_api_key.revoke(key_id, user_id)
            await uow.commit()
