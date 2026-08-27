from collections.abc import Callable

from app.application.ports.crypto import OutboundNetworkPolicy, VersionedSecretCipher
from app.application.ports.inference import InferenceProviderCatalog
from app.domain.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.models.inference import (
    InferenceEndpoint,
    ResourceVisibility,
)
from app.domain.models.scope import OwnerScope, OwnerScopeType
from app.domain.repositories.uow import IUnitOfWork
from app.domain.utils.outbound_url import OutboundURLRejected, resolve_outbound_url


class InferenceEndpointService:
    def __init__(
        self,
        uow_factory: Callable[[], IUnitOfWork],
        cipher: VersionedSecretCipher,
        outbound_policy: OutboundNetworkPolicy,
        provider_catalog: InferenceProviderCatalog,
    ) -> None:
        self._uow_factory = uow_factory
        self._cipher = cipher
        self._outbound_policy = outbound_policy
        self._provider_catalog = provider_catalog

    def validate(self, endpoint: InferenceEndpoint) -> None:
        if not endpoint.display_name.strip():
            raise BadRequestError(
                "推理端点显示名称不能为空",
                error_key="inference.errors.endpointNameRequired",
            )
        if not endpoint.base_url.strip():
            raise BadRequestError(
                "推理端点 Base URL 不能为空",
                error_key="inference.errors.baseUrlRequired",
            )
        try:
            resolve_outbound_url(
                endpoint.base_url,
                allowed_ports=set(self._outbound_policy.allowed_ports),
                allow_private_hosts=set(self._outbound_policy.allow_private_hosts),
                resolve_dns=False,
            )
        except (OutboundURLRejected, ValueError) as exc:
            raise BadRequestError(
                f"推理端点 Base URL 未通过出站安全策略: {exc}",
                error_key="inference.errors.baseUrlRejected",
            ) from exc
        if (
            self._provider_catalog.credential_required(endpoint.provider)
            and not endpoint.credential.strip()
        ):
            raise BadRequestError(
                "该推理 Provider 必须配置凭证",
                error_key="inference.errors.credentialRequired",
                error_params={"provider": endpoint.provider.value},
            )

    @staticmethod
    def _bind_ownership(endpoint: InferenceEndpoint, scope: OwnerScope | None) -> None:
        if endpoint.visibility == ResourceVisibility.GLOBAL:
            endpoint.owner_user_id = None
            endpoint.team_id = None
            return
        if scope is None:
            raise BadRequestError("私有推理端点必须绑定访问作用域")
        endpoint.owner_user_id = scope.user_id
        endpoint.team_id = scope.team_id if scope.type == OwnerScopeType.TEAM else None

    @staticmethod
    def _without_credential(endpoint: InferenceEndpoint) -> InferenceEndpoint:
        return endpoint.model_copy(
            update={
                "credential": "",
                "credential_configured": bool(endpoint.credential.strip()),
            }
        )

    async def list_endpoints(self, scope: OwnerScope | None = None) -> list[InferenceEndpoint]:
        async with self._uow_factory() as uow:
            endpoints = await uow.inference_endpoint.get_all(scope=scope)
        return [self._without_credential(endpoint) for endpoint in endpoints]

    async def get_endpoint(
        self,
        endpoint_id: str,
        *,
        scope: OwnerScope | None = None,
        include_credential: bool = False,
    ) -> InferenceEndpoint:
        async with self._uow_factory() as uow:
            endpoint = await uow.inference_endpoint.get_by_id(endpoint_id, scope=scope)
        if endpoint is None:
            raise NotFoundError("推理端点不存在", error_key="inference.errors.endpointNotFound")
        return endpoint if include_credential else self._without_credential(endpoint)

    async def create_endpoint(
        self,
        endpoint: InferenceEndpoint,
        *,
        scope: OwnerScope | None = None,
        allow_global_mutation: bool = False,
    ) -> InferenceEndpoint:
        if endpoint.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
            raise ForbiddenError("只有管理员可创建全局推理端点")
        self._bind_ownership(endpoint, scope)
        self.validate(endpoint)
        encrypted = (
            self._cipher.encrypt_versioned(endpoint.credential) if endpoint.credential else ""
        )
        async with self._uow_factory() as uow:
            await uow.inference_endpoint.save(endpoint, encrypted)
            await uow.commit()
        return self._without_credential(endpoint)

    async def update_endpoint(
        self,
        endpoint_id: str,
        updates: InferenceEndpoint,
        *,
        scope: OwnerScope | None = None,
        allow_global_mutation: bool = False,
    ) -> InferenceEndpoint:
        async with self._uow_factory() as uow:
            existing = await uow.inference_endpoint.get_by_id(endpoint_id, scope=scope)
            if existing is None:
                raise NotFoundError(
                    "推理端点不存在",
                    error_key="inference.errors.endpointNotFound",
                )
            if existing.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
                raise ForbiddenError("只有管理员可修改全局推理端点")
            if existing.visibility != updates.visibility:
                raise BadRequestError("推理端点可见性不可修改，请新建端点")
            updates.id = endpoint_id
            if not updates.credential.strip():
                updates.credential = existing.credential
            self._bind_ownership(updates, scope)
            self.validate(updates)
            encrypted = (
                self._cipher.encrypt_versioned(updates.credential)
                if updates.credential != existing.credential
                else ""
            )
            await uow.inference_endpoint.save(updates, encrypted)
            await uow.commit()
        return self._without_credential(updates)

    async def delete_endpoint(
        self,
        endpoint_id: str,
        *,
        scope: OwnerScope | None = None,
        allow_global_mutation: bool = False,
    ) -> None:
        async with self._uow_factory() as uow:
            existing = await uow.inference_endpoint.get_by_id(endpoint_id, scope=scope)
            if existing is None:
                raise NotFoundError(
                    "推理端点不存在",
                    error_key="inference.errors.endpointNotFound",
                )
            if existing.visibility == ResourceVisibility.GLOBAL and not allow_global_mutation:
                raise ForbiddenError("只有管理员可删除全局推理端点")
            if await uow.inference_endpoint.count_models(endpoint_id):
                raise BadRequestError(
                    "请先删除该端点下的所有推理模型",
                    error_key="inference.errors.endpointInUse",
                )
            await uow.inference_endpoint.delete_by_id(endpoint_id)
            await uow.commit()
