from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.inference import InferenceBinding, InferencePurpose
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.inference_binding_repository import InferenceBindingRepository
from app.infrastructure.models.inference_binding import InferenceBindingORM


def binding_identity(
    scope: OwnerScope | None,
    purpose: InferencePurpose,
) -> tuple[str, str, str, str | None, str | None]:
    if scope is None:
        return f"global:global:{purpose.value}", "global", "global", None, None
    if scope.type == OwnerScopeType.TEAM:
        if not scope.team_id:
            raise ValueError("团队推理绑定缺少 team_id")
        return (
            f"team:{scope.team_id}:{purpose.value}",
            "team",
            scope.team_id,
            None,
            scope.team_id,
        )
    return (
        f"user:{scope.user_id}:{purpose.value}",
        "user",
        scope.user_id,
        scope.user_id,
        None,
    )


class DBInferenceBindingRepository(InferenceBindingRepository):
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def get_exact(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope | None,
    ) -> InferenceBinding | None:
        binding_id, _, _, _, _ = binding_identity(scope, purpose)
        result = await self.db_session.execute(
            select(InferenceBindingORM).where(InferenceBindingORM.id == binding_id)
        )
        record = result.scalar_one_or_none()
        return record.to_domain() if record else None

    async def get_effective_binding(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope | None,
    ) -> InferenceBinding | None:
        exact = await self.get_exact(purpose, scope)
        if exact is not None or scope is None:
            return exact
        return await self.get_exact(purpose, None)

    async def get_all_effective(
        self,
        scope: OwnerScope | None,
    ) -> list[InferenceBinding]:
        candidates = [
            await self.get_effective_binding(purpose, scope) for purpose in InferencePurpose
        ]
        return [binding for binding in candidates if binding is not None]

    async def save(
        self,
        binding: InferenceBinding,
        scope: OwnerScope | None,
    ) -> None:
        binding_id, scope_type, scope_key, owner_user_id, team_id = binding_identity(
            scope,
            binding.purpose,
        )
        result = await self.db_session.execute(
            select(InferenceBindingORM).where(InferenceBindingORM.id == binding_id)
        )
        record = result.scalar_one_or_none()
        binding.id = binding_id
        binding.owner_user_id = owner_user_id
        binding.team_id = team_id
        binding.updated_at = datetime.now(UTC)
        if record is None:
            self.db_session.add(
                InferenceBindingORM(
                    id=binding_id,
                    scope_type=scope_type,
                    scope_key=scope_key,
                    purpose=binding.purpose.value,
                    owner_user_id=owner_user_id,
                    team_id=team_id,
                    model_id=binding.model_id,
                )
            )
            return
        record.model_id = binding.model_id
        record.updated_at = binding.updated_at

    async def delete_scoped_binding(
        self,
        purpose: InferencePurpose,
        scope: OwnerScope | None,
    ) -> None:
        binding_id, _, _, _, _ = binding_identity(scope, purpose)
        await self.db_session.execute(
            delete(InferenceBindingORM).where(InferenceBindingORM.id == binding_id)
        )
