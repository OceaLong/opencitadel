from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.inference import InferenceModel, ResourceVisibility
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.inference_model_repository import InferenceModelRepository
from app.infrastructure.models.inference_endpoint import InferenceEndpointORM
from app.infrastructure.models.inference_model import InferenceModelORM


class DBInferenceModelRepository(InferenceModelRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    @staticmethod
    def _apply_scope(stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            owner_filter = InferenceModelORM.team_id == scope.team_id
        else:
            owner_filter = (
                InferenceModelORM.owner_user_id == scope.user_id
            ) & InferenceModelORM.team_id.is_(None)
        return stmt.where(or_(InferenceModelORM.visibility == "global", owner_filter))

    @staticmethod
    def _apply_endpoint_scope(stmt, scope: OwnerScope | None):
        if scope is None:
            return stmt
        if scope.type == OwnerScopeType.TEAM:
            owner_filter = InferenceEndpointORM.team_id == scope.team_id
        else:
            owner_filter = (
                InferenceEndpointORM.owner_user_id == scope.user_id
            ) & InferenceEndpointORM.team_id.is_(None)
        return stmt.where(or_(InferenceEndpointORM.visibility == "global", owner_filter))

    def _model_stmt(self, scope: OwnerScope | None = None):
        stmt = select(InferenceModelORM).join(
            InferenceEndpointORM,
            InferenceModelORM.endpoint_id == InferenceEndpointORM.id,
        )
        return self._apply_endpoint_scope(self._apply_scope(stmt, scope), scope)

    async def get_all(self, scope: OwnerScope | None = None) -> list[InferenceModel]:
        result = await self.db_session.execute(
            self._model_stmt(scope).order_by(InferenceModelORM.created_at)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def get_all_global(self) -> list[InferenceModel]:
        result = await self.db_session.execute(
            self._model_stmt()
            .where(InferenceModelORM.visibility == ResourceVisibility.GLOBAL.value)
            .order_by(InferenceModelORM.created_at)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def get_by_id(
        self,
        model_id: str,
        scope: OwnerScope | None = None,
    ) -> InferenceModel | None:
        result = await self.db_session.execute(
            self._model_stmt(scope).where(InferenceModelORM.id == model_id)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_by_endpoint_id(
        self,
        endpoint_id: str,
        scope: OwnerScope | None = None,
    ) -> list[InferenceModel]:
        result = await self.db_session.execute(
            self._model_stmt(scope)
            .where(InferenceModelORM.endpoint_id == endpoint_id)
            .order_by(InferenceModelORM.created_at)
        )
        return [record.to_domain() for record in result.scalars().all()]

    async def save(self, model: InferenceModel) -> None:
        result = await self.db_session.execute(
            select(InferenceModelORM).where(InferenceModelORM.id == model.id)
        )
        record = result.scalar_one_or_none()
        model.updated_at = datetime.now(UTC)
        if record is None:
            self.db_session.add(InferenceModelORM.from_domain(model))
            return
        record.endpoint_id = model.endpoint_id
        record.display_name = model.display_name
        record.model_name = model.model_name
        record.kind = model.kind.value
        record.settings = model.settings.model_dump(mode="json")
        record.input_price_per_million = model.input_price_per_million
        record.output_price_per_million = model.output_price_per_million
        record.extra_params = model.extra_params
        record.capabilities = model.capabilities.model_dump(mode="json")
        record.owner_user_id = model.owner_user_id
        record.team_id = model.team_id
        record.visibility = model.visibility.value
        record.updated_at = model.updated_at

    async def delete_by_id(self, model_id: str) -> None:
        await self.db_session.execute(
            delete(InferenceModelORM).where(InferenceModelORM.id == model_id)
        )

    async def count(self) -> int:
        result = await self.db_session.execute(select(func.count()).select_from(InferenceModelORM))
        return int(result.scalar() or 0)

    async def count_global(self) -> int:
        result = await self.db_session.execute(
            select(func.count())
            .select_from(InferenceModelORM)
            .where(InferenceModelORM.visibility == ResourceVisibility.GLOBAL.value)
        )
        return int(result.scalar() or 0)

    async def count_by_endpoint_id(self, endpoint_id: str) -> int:
        result = await self.db_session.execute(
            select(func.count())
            .select_from(InferenceModelORM)
            .where(InferenceModelORM.endpoint_id == endpoint_id)
        )
        return int(result.scalar() or 0)
