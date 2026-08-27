from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.inference import InferenceEndpoint
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.inference_endpoint_repository import (
    InferenceEndpointRepository,
)
from app.infrastructure.models.inference_endpoint import InferenceEndpointORM
from app.infrastructure.models.inference_model import InferenceModelORM
from app.infrastructure.security.api_key_cipher import ApiKeyCipher, ApiKeyCipherError
from app.infrastructure.security.api_key_encryption import ApiKeyEncryption


class DBInferenceEndpointRepository(InferenceEndpointRepository):
    def __init__(self, db_session: AsyncSession, cipher: ApiKeyCipher) -> None:
        self.db_session = db_session
        self.cipher = cipher

    def _resolve_credential(self, stored: str, encryption: str) -> str:
        if not stored:
            return ""
        if encryption == ApiKeyEncryption.FERNET_V2:
            return self.cipher.decrypt_versioned(stored)
        raise ApiKeyCipherError(f"未知的 credential_encryption 格式: {encryption}")

    @staticmethod
    def _apply_scope(stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            owner_filter = InferenceEndpointORM.team_id == scope.team_id
        else:
            owner_filter = (
                InferenceEndpointORM.owner_user_id == scope.user_id
            ) & InferenceEndpointORM.team_id.is_(None)
        return stmt.where(or_(InferenceEndpointORM.visibility == "global", owner_filter))

    async def get_all(self, scope: OwnerScope | None = None) -> list[InferenceEndpoint]:
        result = await self.db_session.execute(
            self._apply_scope(select(InferenceEndpointORM), scope).order_by(
                InferenceEndpointORM.created_at
            )
        )
        return [
            record.to_domain(
                self._resolve_credential(
                    record.credential,
                    record.credential_encryption,
                )
            )
            for record in result.scalars().all()
        ]

    async def list_hosts(self, scope: OwnerScope | None = None) -> list[str]:
        result = await self.db_session.execute(
            self._apply_scope(select(InferenceEndpointORM.base_url), scope)
        )
        hosts: list[str] = []
        for (base_url,) in result.all():
            if not base_url:
                continue
            candidate = base_url if "://" in base_url else f"//{base_url}"
            if host := (urlparse(candidate).hostname or "").lower():
                hosts.append(host)
        return hosts

    async def get_by_id(
        self,
        endpoint_id: str,
        scope: OwnerScope | None = None,
    ) -> InferenceEndpoint | None:
        result = await self.db_session.execute(
            self._apply_scope(
                select(InferenceEndpointORM).where(InferenceEndpointORM.id == endpoint_id),
                scope,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return record.to_domain(
            self._resolve_credential(record.credential, record.credential_encryption)
        )

    async def save(self, endpoint: InferenceEndpoint, encrypted_credential: str) -> None:
        result = await self.db_session.execute(
            select(InferenceEndpointORM).where(InferenceEndpointORM.id == endpoint.id)
        )
        record = result.scalar_one_or_none()
        endpoint.updated_at = datetime.now(UTC)
        if record is None:
            self.db_session.add(InferenceEndpointORM.from_domain(endpoint, encrypted_credential))
            return
        record.display_name = endpoint.display_name
        record.provider = endpoint.provider.value
        record.base_url = endpoint.base_url
        if encrypted_credential:
            record.credential = encrypted_credential
            record.credential_encryption = ApiKeyEncryption.FERNET_V2
        record.owner_user_id = endpoint.owner_user_id
        record.team_id = endpoint.team_id
        record.visibility = endpoint.visibility.value
        record.updated_at = endpoint.updated_at

    async def delete_by_id(self, endpoint_id: str) -> None:
        await self.db_session.execute(
            delete(InferenceEndpointORM).where(InferenceEndpointORM.id == endpoint_id)
        )

    async def count_models(self, endpoint_id: str) -> int:
        result = await self.db_session.execute(
            select(func.count())
            .select_from(InferenceModelORM)
            .where(InferenceModelORM.endpoint_id == endpoint_id)
        )
        return int(result.scalar() or 0)
