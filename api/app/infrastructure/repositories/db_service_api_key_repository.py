from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.service_api_key import ServiceApiKey
from app.domain.repositories.service_api_key_repository import ServiceApiKeyRepository
from app.infrastructure.models.service_api_key import ServiceApiKeyORM


class DBServiceApiKeyRepository(ServiceApiKeyRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_hash(self, key_hash: str) -> ServiceApiKey | None:
        result = await self.db_session.execute(
            select(ServiceApiKeyORM).where(
                ServiceApiKeyORM.key_hash == key_hash,
                ServiceApiKeyORM.revoked_at.is_(None),
                or_(
                    ServiceApiKeyORM.expires_at.is_(None),
                    ServiceApiKeyORM.expires_at > datetime.now(UTC),
                ),
            )
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_for_user(self, user_id: str) -> list[ServiceApiKey]:
        result = await self.db_session.execute(
            select(ServiceApiKeyORM)
            .where(ServiceApiKeyORM.owner_user_id == user_id)
            .order_by(ServiceApiKeyORM.created_at.desc())
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def save(self, key: ServiceApiKey) -> None:
        record = await self.db_session.get(ServiceApiKeyORM, key.id)
        if record:
            record.update_from_domain(key)
        else:
            self.db_session.add(ServiceApiKeyORM.from_domain(key))

    async def rotate(
        self, key_id: str, user_id: str, *, key_hash: str, prefix: str
    ) -> ServiceApiKey | None:
        """换发密钥材料：仅命中属主本人的未撤销 Key；返回更新后的 Key。"""
        record = await self.db_session.get(ServiceApiKeyORM, key_id)
        if record is None or record.owner_user_id != user_id or record.revoked_at is not None:
            return None
        record.key_hash = key_hash
        record.prefix = prefix
        record.last_used_at = None
        return record.to_domain()

    async def revoke(self, key_id: str, user_id: str) -> None:
        await self.db_session.execute(
            update(ServiceApiKeyORM)
            .where(
                ServiceApiKeyORM.id == key_id,
                ServiceApiKeyORM.owner_user_id == user_id,
                ServiceApiKeyORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
