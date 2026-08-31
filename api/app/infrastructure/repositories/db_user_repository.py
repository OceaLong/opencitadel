from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.user import User
from app.domain.repositories.user_repository import UserRepository
from app.infrastructure.models.user import UserORM

_OWNED_RESOURCE_TABLES = (
    "scheduled_jobs",
    "skills",
    "mcp_servers",
    "a2a_servers",
    "inference_models",
    "inference_endpoints",
    "inference_bindings",
    "sessions",
    "memory_entries",
    "knowledge_bases",
    "codebases",
    "files",
    "llm_token_usages",
)


class DBUserRepository(UserRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_by_id(self, user_id: str) -> User | None:
        result = await self.db_session.execute(select(UserORM).where(UserORM.id == user_id))
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list_by_ids(self, user_ids: list[str]) -> list[User]:
        if not user_ids:
            return []
        result = await self.db_session.execute(select(UserORM).where(UserORM.id.in_(user_ids)))
        return [record.to_domain() for record in result.scalars().all()]

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db_session.execute(
            select(UserORM).where(UserORM.email == email.lower())
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_username(self, username: str) -> User | None:
        result = await self.db_session.execute(select(UserORM).where(UserORM.username == username))
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def list(self, limit: int = 100, offset: int = 0) -> list[User]:
        stmt = (
            select(UserORM)
            .order_by(UserORM.created_at.desc())
            .offset(max(offset, 0))
            .limit(max(1, min(limit, 500)))
        )
        result = await self.db_session.execute(stmt)
        return [record.to_domain() for record in result.scalars().all()]

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(UserORM))
        return int(result.scalar_one() or 0)

    async def count_by_role(self) -> dict[str, int]:
        stmt = select(UserORM.global_role, func.count()).group_by(UserORM.global_role)
        result = await self.db_session.execute(stmt)
        return {role: int(count) for role, count in result.all()}

    async def count_by_status(self) -> dict[str, int]:
        stmt = select(UserORM.status, func.count()).group_by(UserORM.status)
        result = await self.db_session.execute(stmt)
        return {status: int(count) for status, count in result.all()}

    async def delete_owned_resources(self, user_id: str) -> None:
        for table in _OWNED_RESOURCE_TABLES:
            await self.db_session.execute(
                text(f"DELETE FROM {table} WHERE owner_user_id = :user_id"),
                {"user_id": user_id},
            )

    async def revoke_security_material(self, user_id: str) -> None:
        statements = (
            (
                "UPDATE service_api_keys SET revoked_at = CURRENT_TIMESTAMP "
                "WHERE owner_user_id = :user_id AND revoked_at IS NULL"
            ),
            "DELETE FROM oauth_identities WHERE user_id = :user_id",
            "DELETE FROM team_members WHERE user_id = :user_id",
        )
        for statement in statements:
            await self.db_session.execute(text(statement), {"user_id": user_id})

    async def save(self, user: User) -> None:
        user.email = user.email.lower()
        record = await self.db_session.get(UserORM, user.id)
        if record:
            record.update_from_domain(user)
        else:
            self.db_session.add(UserORM.from_domain(user))
        await self.db_session.flush()

    async def delete_by_id(self, user_id: str) -> None:
        await self.db_session.execute(delete(UserORM).where(UserORM.id == user_id))
