#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.domain.models.service_api_key import ServiceApiKey


class CreateServiceApiKeyRequest(BaseModel):
    name: str


class ServiceApiKeyResponse(BaseModel):
    id: str
    name: str
    prefix: str
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    created_at: datetime

    @classmethod
    def from_domain(cls, key: ServiceApiKey) -> "ServiceApiKeyResponse":
        return cls(
            id=key.id,
            name=key.name,
            prefix=key.prefix,
            last_used_at=key.last_used_at,
            revoked_at=key.revoked_at,
            created_at=key.created_at,
        )


class CreatedServiceApiKeyResponse(ServiceApiKeyResponse):
    plaintext: str


class ListServiceApiKeysResponse(BaseModel):
    keys: list[ServiceApiKeyResponse]
