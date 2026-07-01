#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Optional

from fastapi import APIRouter, Depends

from app.application.services.service_api_key_service import ServiceApiKeyService
from app.interfaces.auth_dependencies import get_current_principal
from app.interfaces.schemas import Response
from app.interfaces.schemas.service_api_key import (
    CreateServiceApiKeyRequest,
    CreatedServiceApiKeyResponse,
    ListServiceApiKeysResponse,
    ServiceApiKeyResponse,
)
from app.interfaces.service_dependencies import get_service_api_key_service

router = APIRouter(prefix="/service-keys", tags=["服务 API Key"])


@router.get("", response_model=Response[ListServiceApiKeysResponse])
async def list_service_keys(
        principal=Depends(get_current_principal),
        service: ServiceApiKeyService = Depends(get_service_api_key_service),
) -> Response[ListServiceApiKeysResponse]:
    keys = await service.list_keys(principal.user_id)
    return Response.success(data=ListServiceApiKeysResponse(keys=[ServiceApiKeyResponse.from_domain(k) for k in keys]))


@router.post("", response_model=Response[CreatedServiceApiKeyResponse])
async def create_service_key(
        request: CreateServiceApiKeyRequest,
        principal=Depends(get_current_principal),
        service: ServiceApiKeyService = Depends(get_service_api_key_service),
) -> Response[CreatedServiceApiKeyResponse]:
    created = await service.create_key(user_id=principal.user_id, name=request.name)
    response = CreatedServiceApiKeyResponse(
        **ServiceApiKeyResponse.from_domain(created.key).model_dump(),
        plaintext=created.plaintext,
    )
    return Response.success(data=response, msg="服务 API Key 已创建，请立即保存明文")


@router.delete("/{key_id}", response_model=Response[Optional[dict]])
async def revoke_service_key(
        key_id: str,
        principal=Depends(get_current_principal),
        service: ServiceApiKeyService = Depends(get_service_api_key_service),
) -> Response[Optional[dict]]:
    await service.revoke_key(user_id=principal.user_id, key_id=key_id)
    return Response.success(msg="服务 API Key 已吊销")
