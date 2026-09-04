from collections.abc import Callable

from app.application.ports.inference import (
    EmbeddingFactoryPort,
    InferenceProviderCatalog,
    ModelClientFactoryPort,
    UnsupportedInferenceCombination,
)
from app.domain.errors import (
    AppException,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.domain.models.inference import (
    PLATFORM_EMBEDDING_DIMENSIONS,
    InferenceEndpoint,
    InferenceModel,
    InferenceModelKind,
    InferenceProbeResult,
    InferenceProbeStatus,
    InferencePurpose,
    ResolvedInferenceModel,
    ResourceVisibility,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork


class InferenceModelService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        provider_catalog: InferenceProviderCatalog,
        model_client_factory: ModelClientFactoryPort,
        embedding_factory: EmbeddingFactoryPort,
    ) -> None:
        self._uow_factory = uow_factory
        self._provider_catalog = provider_catalog
        self._model_client_factory = model_client_factory
        self._embedding_factory = embedding_factory

    def validate(self, model: InferenceModel, endpoint: InferenceEndpoint) -> None:
        if not model.endpoint_id.strip():
            raise BadRequestError("必须选择推理端点")
        if not model.display_name.strip():
            raise BadRequestError("推理模型显示名称不能为空")
        if not model.model_name.strip():
            raise BadRequestError("Provider 模型名称不能为空")
        try:
            self._provider_catalog.ensure_kind_supported(endpoint.provider, model.kind)
        except UnsupportedInferenceCombination as exc:
            raise BadRequestError(
                str(exc),
                error_key="inference.errors.unsupportedProviderKind",
                error_params={
                    "provider": endpoint.provider.value,
                    "kind": model.kind.value,
                },
            ) from exc

    @staticmethod
    def _bind_ownership(model: InferenceModel, scope: OwnerScope | None) -> None:
        if model.visibility == ResourceVisibility.GLOBAL:
            model.owner_user_id = None
            model.team_id = None
            return
        if scope is None:
            raise BadRequestError("私有推理模型必须绑定访问作用域")
        model.owner_user_id = scope.user_id
        model.team_id = scope.team_id if scope.type == OwnerScopeType.TEAM else None

    async def list_models(
        self,
        *,
        scope: OwnerScope | None = None,
        kind: InferenceModelKind | None = None,
    ) -> list[InferenceModel]:
        async with self._uow_factory() as uow:
            models = await uow.inference_model.get_all(scope=scope)
        return [model for model in models if kind is None or model.kind == kind]

    async def get_model(
        self,
        model_id: str,
        *,
        scope: OwnerScope | None = None,
    ) -> InferenceModel:
        async with self._uow_factory() as uow:
            model = await uow.inference_model.get_by_id(model_id, scope=scope)
        if model is None:
            raise NotFoundError("推理模型不存在", error_key="inference.errors.modelNotFound")
        return model

    async def create_model(
        self,
        model: InferenceModel,
        *,
        scope: OwnerScope | None = None,
        allow_global_mutation: bool = False,
    ) -> InferenceModel:
        if model.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
            raise ForbiddenError("只有管理员可创建全局推理模型")
        self._bind_ownership(model, scope)
        async with self._uow_factory() as uow:
            endpoint = await uow.inference_endpoint.get_by_id(model.endpoint_id, scope=scope)
            if endpoint is None:
                raise BadRequestError(
                    "推理端点不存在或不可访问",
                    error_key="inference.errors.endpointNotFound",
                )
            self.validate(model, endpoint)
            await uow.inference_model.save(model)
            await uow.commit()
        return model

    async def update_model(
        self,
        model_id: str,
        updates: InferenceModel,
        *,
        scope: OwnerScope | None = None,
        allow_global_mutation: bool = False,
    ) -> InferenceModel:
        async with self._uow_factory() as uow:
            existing = await uow.inference_model.get_by_id(model_id, scope=scope)
            if existing is None:
                raise NotFoundError(
                    "推理模型不存在",
                    error_key="inference.errors.modelNotFound",
                )
            if existing.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
                raise ForbiddenError("只有管理员可修改全局推理模型")
            if existing.visibility != updates.visibility:
                raise BadRequestError("推理模型可见性不可修改，请新建模型")
            endpoint = await uow.inference_endpoint.get_by_id(updates.endpoint_id, scope=scope)
            if endpoint is None:
                raise BadRequestError(
                    "推理端点不存在或不可访问",
                    error_key="inference.errors.endpointNotFound",
                )
            updates.id = model_id
            self._bind_ownership(updates, scope)
            self.validate(updates, endpoint)
            await uow.inference_model.save(updates)
            await uow.commit()
        return updates

    async def delete_model(
        self,
        model_id: str,
        *,
        scope: OwnerScope | None = None,
        allow_global_mutation: bool = False,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.inference_model.get_by_id(model_id, scope=scope)
            if existing is None:
                raise NotFoundError(
                    "推理模型不存在",
                    error_key="inference.errors.modelNotFound",
                )
            if existing.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
                raise ForbiddenError("只有管理员可删除全局推理模型")
            # Fail with a semantic error instead of surfacing the FK violation
            # as an opaque 500: bindings must be released first.
            if await uow.inference_binding.count_for_model(model_id):
                raise BadRequestError(
                    "仍有推理绑定正在使用该模型，请先解除绑定",
                    error_key="inference.errors.modelInUse",
                )
            await uow.inference_model.delete_by_id(model_id)
            await uow.commit()

    async def resolve_model(
        self,
        model_id: str,
        *,
        scope: OwnerScope | None,
    ) -> ResolvedInferenceModel:
        async with self._uow_factory() as uow:
            model = await uow.inference_model.get_by_id(model_id, scope=scope)
            if model is None:
                raise NotFoundError(
                    "推理模型不存在或不可访问",
                    error_key="inference.errors.modelNotFound",
                )
            endpoint = await uow.inference_endpoint.get_by_id(model.endpoint_id, scope=scope)
            if endpoint is None:
                raise ConflictError(
                    "推理模型所属端点不存在或不可访问",
                    error_key="inference.errors.endpointNotFound",
                )
            self.ensure_invokable(endpoint)
            return ResolvedInferenceModel(model=model, endpoint=endpoint)

    async def resolve_chat(
        self,
        model_id: str | None = None,
        *,
        scope: OwnerScope | None,
    ) -> ResolvedInferenceModel:
        if model_id:
            resolved = await self.resolve_model(model_id, scope=scope)
            if resolved.model.kind != InferenceModelKind.CHAT:
                raise BadRequestError(
                    "Chat 调用只能选择 Chat 模型",
                    error_key="inference.errors.bindingKindMismatch",
                )
            return resolved
        async with self._uow_factory() as uow:
            binding = await uow.inference_binding.get_effective_binding(
                InferencePurpose.CHAT,
                scope,
            )
            if binding is None:
                raise ConflictError(
                    "Chat 推理尚未配置模型绑定",
                    error_key="inference.errors.bindingNotConfigured",
                    error_params={"purpose": InferencePurpose.CHAT.value},
                )
            model = await uow.inference_model.get_by_id(binding.model_id, scope=scope)
            if model is None or model.kind != InferenceModelKind.CHAT:
                raise ConflictError(
                    "Chat 推理绑定不可用",
                    error_key="inference.errors.bindingModelUnavailable",
                )
            endpoint = await uow.inference_endpoint.get_by_id(model.endpoint_id, scope=scope)
            if endpoint is None:
                raise ConflictError(
                    "推理模型所属端点不存在或不可访问",
                    error_key="inference.errors.endpointNotFound",
                )
            self.ensure_invokable(endpoint)
            return ResolvedInferenceModel(model=model, endpoint=endpoint, binding=binding)

    async def list_resolved_chat_models(
        self,
        *,
        scope: OwnerScope | None,
    ) -> list[ResolvedInferenceModel]:
        async with self._uow_factory() as uow:
            models = await uow.inference_model.get_all(scope=scope)
            resolved: list[ResolvedInferenceModel] = []
            for model in models:
                if model.kind != InferenceModelKind.CHAT:
                    continue
                endpoint = await uow.inference_endpoint.get_by_id(
                    model.endpoint_id,
                    scope=scope,
                )
                if endpoint is None:
                    continue
                try:
                    self.ensure_invokable(endpoint)
                except BadRequestError:
                    continue
                resolved.append(ResolvedInferenceModel(model=model, endpoint=endpoint))
            return resolved

    async def probe_model(
        self,
        model_id: str,
        *,
        scope: OwnerScope | None,
    ) -> InferenceProbeResult:
        resolved = await self.resolve_model(model_id, scope=scope)
        try:
            if resolved.model.kind is InferenceModelKind.CHAT:
                adapter = self._model_client_factory.create_model_client(
                    resolved,
                    thinking_enabled=False,
                )
                result = await adapter.invoke([{"role": "user", "content": "Reply with OK."}])
                if not result.get("content") and not result.get("tool_calls"):
                    return InferenceProbeResult(
                        status=InferenceProbeStatus.ERROR,
                        message="Chat 推理探测返回空响应",
                        error_key="inference.errors.emptyProbeResponse",
                    )
            else:
                adapter = self._embedding_factory.create_embedding(resolved)
                vectors = await adapter.embed_batch(["OpenCitadel inference probe"])
                if len(vectors) != 1 or len(vectors[0]) != PLATFORM_EMBEDDING_DIMENSIONS:
                    return InferenceProbeResult(
                        status=InferenceProbeStatus.ERROR,
                        message="Embedding 推理探测返回了无效向量维度",
                        error_key="inference.errors.embeddingDimensionMismatch",
                    )
        except AppException as exc:
            return InferenceProbeResult(
                status=InferenceProbeStatus.ERROR,
                message=exc.msg,
                error_key=exc.error_key,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            return InferenceProbeResult(
                status=InferenceProbeStatus.ERROR,
                message=str(exc),
                error_key="inference.errors.probeFailed",
            )
        return InferenceProbeResult(
            status=InferenceProbeStatus.OK,
            message="推理模型探测成功",
        )

    def ensure_invokable(self, endpoint: InferenceEndpoint) -> None:
        if (
            self._provider_catalog.credential_required(endpoint.provider)
            and not endpoint.credential.strip()
        ):
            raise BadRequestError(
                "推理端点未配置凭证",
                error_key="inference.errors.credentialRequired",
                error_params={"provider": endpoint.provider.value},
            )
