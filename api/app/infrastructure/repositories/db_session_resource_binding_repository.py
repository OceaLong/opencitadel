from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.resource_bindings import ResourceKind, SessionResourceBinding
from app.domain.repositories.session_resource_binding_repository import (
    SessionResourceBindingRepository,
)
from app.infrastructure.models.session_resource_binding import (
    SessionResourceBindingORM,
)


class DBSessionResourceBindingRepository(SessionResourceBindingRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def add_binding(
        self,
        binding: SessionResourceBinding,
    ) -> SessionResourceBinding:
        record = SessionResourceBindingORM.from_domain(binding)
        self.db_session.add(record)
        await self.db_session.flush()
        return record.to_domain()

    async def get_current_binding(
        self,
        session_id: str,
        resource_kind: ResourceKind,
        *,
        for_update: bool = False,
    ) -> SessionResourceBinding | None:
        stmt = select(SessionResourceBindingORM).where(
            SessionResourceBindingORM.session_id == session_id,
            SessionResourceBindingORM.resource_kind == ResourceKind(resource_kind).value,
            SessionResourceBindingORM.is_current.is_(True),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db_session.execute(stmt)
        record = result.scalar_one_or_none()
        return record.to_domain() if record is not None else None

    async def list_current_bindings(
        self,
        session_id: str,
    ) -> list[SessionResourceBinding]:
        result = await self.db_session.execute(
            select(SessionResourceBindingORM)
            .where(
                SessionResourceBindingORM.session_id == session_id,
                SessionResourceBindingORM.is_current.is_(True),
            )
            .order_by(SessionResourceBindingORM.resource_kind.asc())
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def list_bindings(
        self,
        session_id: str,
        resource_kind: ResourceKind | None = None,
    ) -> list[SessionResourceBinding]:
        stmt = select(SessionResourceBindingORM).where(
            SessionResourceBindingORM.session_id == session_id
        )
        if resource_kind is not None:
            stmt = stmt.where(
                SessionResourceBindingORM.resource_kind == ResourceKind(resource_kind).value
            )
        result = await self.db_session.execute(
            stmt.order_by(
                SessionResourceBindingORM.created_at.asc(),
                SessionResourceBindingORM.id.asc(),
            )
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def replace_current_binding(
        self,
        current: SessionResourceBinding,
        replacement: SessionResourceBinding,
    ) -> SessionResourceBinding:
        if (
            replacement.session_id != current.session_id
            or replacement.resource_kind != current.resource_kind
            or replacement.resource_id != current.resource_id
            or replacement.supersedes_binding_id != current.id
            or not replacement.is_current
        ):
            raise ValueError("invalid replacement binding lineage")
        result = await self.db_session.execute(
            select(SessionResourceBindingORM)
            .where(SessionResourceBindingORM.id == current.id)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None or not record.is_current:
            raise ValueError("current binding changed before replacement")
        record.is_current = False
        await self.db_session.flush()
        return await self.add_binding(replacement)
