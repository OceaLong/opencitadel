from collections.abc import Callable

from app.application.ports.inference import InferenceProviderCatalog
from app.domain.errors import BadRequestError, ConflictError, NotFoundError
from app.domain.models.inference import (
    InferenceBinding,
    InferencePurpose,
    ResolvedInferenceModel,
    purpose_accepts_kind,
)
from app.domain.models.scope import OwnerScope
from app.domain.repositories.uow import IUnitOfWork


class InferenceBindingService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        provider_catalog: InferenceProviderCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._provider_catalog = provider_catalog

    def _ensure_invokable(self, endpoint) -> None:
        if (
            self._provider_catalog.credential_required(endpoint.provider)
            and not endpoint.credential.strip()
        ):
            raise BadRequestError(
                "推理端点未配置凭证",
                error_key="inference.errors.credentialRequired",
                error_params={"provider": endpoint.provider.value},
            )

    async def set_binding(
        self,
        purpose: InferencePurpose,
        model_id: str,
        *,
        scope: OwnerScope | None,
    ) -> InferenceBinding:
        async with self._uow_factory() as uow:
            model = await uow.inference_model.get_by_id(model_id, scope=scope)
            if model is None:
                raise NotFoundError(
                    "推理模型不存在或不可访问",
                    error_key="inference.errors.modelNotFound",
                )
            if not purpose_accepts_kind(purpose, model.kind):
                raise BadRequestError(
                    "推理用途与模型类型不匹配",
                    error_key="inference.errors.bindingKindMismatch",
                    error_params={"purpose": purpose.value, "kind": model.kind.value},
                )
            endpoint = await uow.inference_endpoint.get_by_id(model.endpoint_id, scope=scope)
            if endpoint is None:
                raise BadRequestError(
                    "推理端点不存在或不可访问",
                    error_key="inference.errors.endpointNotFound",
                )
            self._ensure_invokable(endpoint)
            binding = InferenceBinding(purpose=purpose, model_id=model.id)
            await uow.inference_binding.save(binding, scope)
            await uow.commit()
        return binding

    async def delete_binding(
        self,
        purpose: InferencePurpose,
        *,
        scope: OwnerScope | None,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.inference_binding.delete_scoped_binding(purpose, scope)
            await uow.commit()

    async def list_bindings(
        self,
        *,
        scope: OwnerScope | None,
    ) -> list[InferenceBinding]:
        async with self._uow_factory() as uow:
            return await uow.inference_binding.get_all_effective(scope)

    async def resolve(
        self,
        purpose: InferencePurpose,
        *,
        scope: OwnerScope | None,
    ) -> ResolvedInferenceModel:
        async with self._uow_factory() as uow:
            binding = await uow.inference_binding.get_effective_binding(purpose, scope)
            if binding is None and purpose == InferencePurpose.RERANK:
                binding = await uow.inference_binding.get_effective_binding(
                    InferencePurpose.CHAT,
                    scope,
                )
            if binding is None:
                raise ConflictError(
                    "推理用途尚未配置模型绑定",
                    error_key="inference.errors.bindingNotConfigured",
                    error_params={"purpose": purpose.value},
                )
            model = await uow.inference_model.get_by_id(binding.model_id, scope=scope)
            if model is None:
                raise ConflictError(
                    "推理绑定引用的模型不存在或不可访问",
                    error_key="inference.errors.bindingModelUnavailable",
                    error_params={"purpose": purpose.value},
                )
            if not purpose_accepts_kind(purpose, model.kind):
                raise ConflictError(
                    "推理绑定的模型类型无效",
                    error_key="inference.errors.bindingKindMismatch",
                    error_params={"purpose": purpose.value, "kind": model.kind.value},
                )
            endpoint = await uow.inference_endpoint.get_by_id(model.endpoint_id, scope=scope)
            if endpoint is None:
                raise ConflictError(
                    "推理模型所属端点不存在或不可访问",
                    error_key="inference.errors.endpointNotFound",
                )
            self._ensure_invokable(endpoint)
            return ResolvedInferenceModel(
                model=model,
                endpoint=endpoint,
                binding=binding,
            )
